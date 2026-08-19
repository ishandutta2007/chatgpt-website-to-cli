/**
 * Chatgpt CLI Bridge - Content Script
 *
 * Runs inside chatgpt.com pages. Handles DOM operations requested
 * by the background service worker (forwarded from the Python CLI).
 */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const { type } = message;

  const handlers = {
    send_prompt: () => handleSendPrompt(message.prompt),
    check_response_status: () => handleCheckResponseStatus(),
    extract_last_code_block: () => handleExtractLastCodeBlock(),
    extract_full_response: () => handleExtractFullResponse(),
  };

  const handler = handlers[type];
  if (!handler) return false;

  handler()
    .then((data) => sendResponse(data))
    .catch((err) => sendResponse({ __error: true, error: err.message }));

  return true; // Keep message channel open for async sendResponse
});

// ── Prompt Submission ────────────────────────────────────────────────────

async function handleSendPrompt(prompt) {
  // Try multiple selectors to find the prompt input
  const selectors = [
    'textarea',
    'div[contenteditable="true"]',
    '[role="textbox"]',
  ];

  let inputElement = null;
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el && isVisible(el)) {
      inputElement = el;
      break;
    }
  }

  if (!inputElement) {
    throw new Error(
      'Could not find the prompt input element. Make sure chatgpt.com is fully loaded and you are logged in.'
    );
  }

  // Set the prompt text using React-compatible methods
  const tag = inputElement.tagName.toLowerCase();

  if (tag === 'textarea') {
    // React overrides the value setter, so we need the native one
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      'value'
    ).set;
    nativeSetter.call(inputElement, prompt);
    inputElement.dispatchEvent(new Event('input', { bubbles: true }));
    inputElement.dispatchEvent(new Event('change', { bubbles: true }));
  } else if (inputElement.isContentEditable) {
    inputElement.focus();
    // Select all existing content and replace
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(inputElement);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('insertText', false, prompt);
  } else {
    // Generic input fallback
    inputElement.value = prompt;
    inputElement.dispatchEvent(new Event('input', { bubbles: true }));
    inputElement.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Wait for React/framework to process the input
  await sleep(500);

  // Submit by pressing Enter
  const enterEventInit = {
    key: 'Enter',
    code: 'Enter',
    keyCode: 13,
    which: 13,
    bubbles: true,
    cancelable: true,
  };
  inputElement.dispatchEvent(new KeyboardEvent('keydown', enterEventInit));
  inputElement.dispatchEvent(new KeyboardEvent('keypress', enterEventInit));
  inputElement.dispatchEvent(new KeyboardEvent('keyup', enterEventInit));

  // Also try submitting via a nearby submit button as fallback
  await sleep(300);
  const submitBtn = document.querySelector(
    'button[type="submit"], button[aria-label*="Send" i], button[data-testid*="send" i]'
  );
  if (submitBtn && isVisible(submitBtn)) {
    submitBtn.click();
  }

  return { submitted: true };
}

// ── Response Status Check ────────────────────────────────────────────────

async function handleCheckResponseStatus() {
  // Check for loading/generating indicators
  const stopSelectors = [
    'button[aria-label*="Stop" i]',
    '[data-testid="stop-button"]',
    'button[aria-label*="stop generating" i]',
  ];
  const spinnerSelectors = ['.animate-spin', 'svg.animate-spin', '[class*="loading"]'];

  let generating = false;

  for (const sel of stopSelectors) {
    const el = document.querySelector(sel);
    if (el && isVisible(el)) {
      generating = true;
      break;
    }
  }

  if (!generating) {
    for (const sel of spinnerSelectors) {
      const el = document.querySelector(sel);
      if (el && isVisible(el)) {
        generating = true;
        break;
      }
    }
  }

  // Count code blocks
  const codeBlocks = document.querySelectorAll('pre code, pre, div.code-block code');
  const visibleCodeBlocks = Array.from(codeBlocks).filter(isVisible);

  // Check for response content
  const responseSelectors = [
    'div[data-testid="message-content"]',
    'div.message-content',
    'div.markdown',
    'div[class*="response"]',
    'div[class*="message"]',
  ];

  let hasResponse = false;
  for (const sel of responseSelectors) {
    const els = document.querySelectorAll(sel);
    if (els.length > 0) {
      hasResponse = true;
      break;
    }
  }

  return {
    generating,
    codeBlockCount: visibleCodeBlocks.length,
    hasResponse,
  };
}

// ── Code Block Extraction ────────────────────────────────────────────────

async function handleExtractLastCodeBlock() {
  const codeBlocks = Array.from(
    document.querySelectorAll('pre code, pre, code.hljs, div.code-block code')
  ).filter(isVisible);

  if (codeBlocks.length === 0) {
    return { text: null, error: 'No code blocks found' };
  }

  const lastBlock = codeBlocks[codeBlocks.length - 1];

  // Strategy 1: Try to find and click a copy button near the code block
  const copiedText = await tryCopyButton(lastBlock);
  if (copiedText) {
    return { text: copiedText, method: 'copy_button' };
  }

  // Strategy 2: Read text content directly
  const text = lastBlock.innerText || lastBlock.textContent;
  if (text && text.trim()) {
    return { text: text.trim(), method: 'innerText' };
  }

  return { text: null, error: 'Code block found but empty' };
}

async function tryCopyButton(codeBlockElement) {
  // Walk up the DOM looking for a copy button
  let container = codeBlockElement;
  for (let i = 0; i < 6; i++) {
    container = container.parentElement;
    if (!container || container === document.body) break;

    const btn = container.querySelector(
      'button[aria-label*="Copy" i], button[title*="Copy" i], button.copy-button, button[class*="copy" i]'
    );

    if (btn && isVisible(btn)) {
      try {
        btn.click();
        await sleep(300);
        const text = await navigator.clipboard.readText();
        if (text && text.trim()) {
          return text.trim();
        }
      } catch (e) {
        console.warn('[Chatgpt CLI Bridge] Clipboard read failed:', e);
      }
    }
  }
  return null;
}

// ── Full Response Extraction ─────────────────────────────────────────────

async function handleExtractFullResponse() {
  const selectors = [
    'div[data-testid="message-content"]',
    'div.message-content',
    'div.markdown',
    'div[class*="response"]',
  ];

  for (const sel of selectors) {
    const elements = Array.from(document.querySelectorAll(sel)).filter(isVisible);
    if (elements.length > 0) {
      const lastEl = elements[elements.length - 1];
      const text = lastEl.innerText || lastEl.textContent;
      if (text && text.trim()) {
        return { text: text.trim() };
      }
    }
  }

  return { text: null, error: 'No response containers found' };
}

// ── Utilities ────────────────────────────────────────────────────────────

function isVisible(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  return (
    style.display !== 'none' &&
    style.visibility !== 'hidden' &&
    style.opacity !== '0' &&
    el.offsetParent !== null
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
