"""
chatgpt-website-to-cli
~~~~~~~~~~~~~~~~~~~~

RPA-powered CLI tool that converts Chatgpt website (chatgpt.com) interactions
into a command-line interface using a paired browser extension.
"""

__version__ = "0.1.0"
__author__ = "Ishan Dutta"

from chatgpt_website_to_cli.browser import ChatgptBridge
from chatgpt_website_to_cli.chatgpt import ChatgptAutomation

__all__ = ["ChatgptBridge", "ChatgptAutomation", "__version__"]
