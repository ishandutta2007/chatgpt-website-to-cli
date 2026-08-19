"""
Chatgpt Automation (Extension-based)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

High-level orchestration of Chatgpt interactions via the paired browser extension.
All DOM operations are performed by the extension's content script running
inside the user's real Edge browser session.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from chatgpt_website_to_cli.browser import ChatgptBridge

logger = logging.getLogger(__name__)


class ChatgptAutomation:
    """Orchestrates Chatgpt interactions through the browser extension bridge.

    Sends high-level commands to the extension, which handles the actual
    DOM manipulation inside the user's logged-in Edge browser.
    """

    def __init__(
        self,
        bridge: ChatgptBridge,
        max_wait_seconds: int = 180,
        poll_interval: float = 3.0,
    ) -> None:
        self.bridge = bridge
        self.max_wait_seconds = max_wait_seconds
        self.poll_interval = poll_interval

    async def find_or_open_chatgpt_tab(self) -> int:
        """Find an existing Chatgpt tab or open a new one.

        Returns:
            The tab ID of the Chatgpt tab.
        """
        # Search for existing Chatgpt tabs
        result = await self.bridge.send_command("find_chatgpt_tab")
        tabs = result.get("tabs", [])

        if tabs:
            tab = tabs[0]
            tab_id = tab["id"]
            logger.info("Found Chatgpt tab: %s (id=%d)", tab.get("title", ""), tab_id)
            # Activate the tab and bring window to focus
            await self.bridge.send_command("activate_tab", tabId=tab_id)
            return tab_id
        else:
            logger.info("No Chatgpt tab found. Opening a new one...")
            result = await self.bridge.send_command("open_chatgpt_tab", timeout=30)
            tab_id = result["tabId"]
            logger.info("Opened new Chatgpt tab (id=%d)", tab_id)
            return tab_id

    async def send_prompt(
        self,
        prompt_text: str,
        max_retries: int = 5,
        retry_delay: float = 3.0,
    ) -> None:
        """Send a prompt to the Chatgpt input box.

        The extension's content script handles finding the input element,
        setting the value (React-compatible), and submitting with Enter.

        Retries up to ``max_retries`` times on transient failures
        (timeouts, disconnections, extension errors).

        Args:
            prompt_text: The full prompt text to send.
            max_retries: Maximum number of send attempts.
            retry_delay: Seconds to wait between retries.
        """
        logger.info("Sending prompt to Chatgpt (%d chars)...", len(prompt_text))

        for attempt in range(1, max_retries + 1):
            try:
                await self.bridge.send_command(
                    "send_prompt",
                    prompt=prompt_text,
                    timeout=30,
                )
                logger.info(
                    "Prompt submitted successfully (attempt %d/%d).",
                    attempt, max_retries,
                )
                return
            except (ConnectionError, TimeoutError, RuntimeError, OSError) as exc:
                logger.warning(
                    "send_prompt failed (attempt %d/%d): %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    logger.info(
                        "Retrying send_prompt in %.1fs...", retry_delay
                    )
                    await asyncio.sleep(retry_delay)

        raise RuntimeError(
            f"Failed to send prompt after {max_retries} attempts."
        )

    async def wait_for_response(self) -> None:
        """Wait for Chatgpt to finish generating its response.

        Polls the extension's content script for response status until
        generation is complete or the timeout is reached.
        """

        logger.info("Waiting for Chatgpt response (up to %ds)...", self.max_wait_seconds)

        # Initial delay to let generation start
        await asyncio.sleep(5)

        start_time = asyncio.get_event_loop().time()
        was_generating = False
        last_code_block_count = 0
        stable_count = 0

        while (asyncio.get_event_loop().time() - start_time) < self.max_wait_seconds:
            try:
                status = await self.bridge.send_command(
                    "check_response_status", timeout=10
                )
            except Exception as exc:
                logger.debug("Status check failed: %s", exc)
                await asyncio.sleep(self.poll_interval)
                continue

            generating = status.get("generating", False)
            code_block_count = status.get("codeBlockCount", 0)
            has_response = status.get("hasResponse", False)
            elapsed = int(asyncio.get_event_loop().time() - start_time)

            if generating:
                was_generating = True
                stable_count = 0
                logger.debug("Still generating... (%ds elapsed)", elapsed)
            elif was_generating:
                # Was generating but stopped — response is likely complete
                logger.info("Generation complete after %ds.", elapsed)
                await asyncio.sleep(2)  # Grace period for DOM to settle
                return
            elif has_response:
                # Response appeared without us detecting a loading state
                # Wait for code block count to stabilize
                if code_block_count == last_code_block_count and code_block_count > 0:
                    stable_count += 1
                    if stable_count >= 3:
                        logger.info(
                            "Response appears stable (%d code blocks, %ds elapsed).",
                            code_block_count,
                            elapsed,
                        )
                        await asyncio.sleep(2)
                        return
                else:
                    stable_count = 0

            last_code_block_count = code_block_count
            await asyncio.sleep(self.poll_interval)

        if not was_generating:
            logger.warning(
                "No loading indicator detected. Response may already be present."
            )
            await asyncio.sleep(5)
        else:
            logger.warning(
                "Timed out after %ds while waiting for response.", self.max_wait_seconds
            )

    async def extract_last_code_block(
        self,
        max_retries: int = 5,
        retry_delay: float = 3.0,
    ) -> Optional[str]:
        """Extract the text of the last code block from the Chatgpt response.

        The extension tries multiple strategies:
        1. Click the copy button on the code block
        2. Read innerText directly

        Retries up to ``max_retries`` times on transient failures (empty
        result, connection errors, or extension errors) with a delay between
        each attempt.

        Args:
            max_retries: Maximum number of extraction attempts.
            retry_delay: Seconds to wait between retries.

        Returns:
            The code block text, or None if no code blocks were found after
            all retries.
        """
        for attempt in range(1, max_retries + 1):
            try:
                result = await self.bridge.send_command(
                    "extract_last_code_block", timeout=15
                )
                text = result.get("text")
                if text:
                    method = result.get("method", "unknown")
                    logger.info(
                        "Extracted code block (%d chars) via %s (attempt %d/%d).",
                        len(text), method, attempt, max_retries,
                    )
                    return text.strip()
                else:
                    logger.warning(
                        "No code blocks found (attempt %d/%d).",
                        attempt, max_retries,
                    )
            except (RuntimeError, ConnectionError, OSError) as exc:
                logger.warning(
                    "Code block extraction failed (attempt %d/%d): %s",
                    attempt, max_retries, exc,
                )

            if attempt < max_retries:
                logger.info(
                    "Retrying code block extraction in %.1fs...", retry_delay
                )
                await asyncio.sleep(retry_delay)

        logger.warning(
            "Could not extract code block after %d attempts.", max_retries
        )
        return None

    async def extract_full_response(
        self,
        max_retries: int = 5,
        retry_delay: float = 3.0,
    ) -> Optional[str]:
        """Extract the full text of the latest Chatgpt response.

        Retries up to ``max_retries`` times on transient failures.

        Args:
            max_retries: Maximum number of extraction attempts.
            retry_delay: Seconds to wait between retries.

        Returns:
            The full response text, or None if nothing was found after all
            retries.
        """
        for attempt in range(1, max_retries + 1):
            try:
                result = await self.bridge.send_command(
                    "extract_full_response", timeout=15
                )
                text = result.get("text")
                if text:
                    logger.info(
                        "Extracted full response (%d chars, attempt %d/%d).",
                        len(text), attempt, max_retries,
                    )
                    return text.strip()
                else:
                    logger.warning(
                        "No response content found (attempt %d/%d).",
                        attempt, max_retries,
                    )
            except (RuntimeError, ConnectionError, OSError) as exc:
                logger.warning(
                    "Full response extraction failed (attempt %d/%d): %s",
                    attempt, max_retries, exc,
                )

            if attempt < max_retries:
                logger.info(
                    "Retrying full response extraction in %.1fs...", retry_delay
                )
                await asyncio.sleep(retry_delay)

        logger.warning(
            "Could not extract full response after %d attempts.", max_retries
        )
        return None
