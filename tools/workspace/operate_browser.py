import asyncio
import json
import os
import shlex
import time
from typing import Dict, Any, List, Union

from playwright.async_api import Page
from tools.base import Tool
from providers.computer.client import ComputerClient


_pages = []
_current_idx = 0

def get_current_page() -> Page:
    if not _pages:
        raise Exception("No active browser page available")
    
    if _current_idx >= len(_pages):
        raise Exception(f"Tab index {_current_idx} out of range, current tabs: {len(_pages)}")

    return _pages[_current_idx]

class OperateBrowserTool(Tool):
    """Operate browser to perform web interaction actions"""
    
    name = "operate_browser"
    
    def __init__(self):
        super().__init__()
        self.computer_client = ComputerClient.get_instance()
        self.os_workspace = self.computer_client.get_os_workspace()

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Operate browser to perform web interaction actions, including navigation, dialog interaction, execute JavaScript, etc. "
                    "Supports multi-tab management with operations to open new tab, switch tab, list tabs, and close tab. "
                    "Max supported tabs: 10.",
                "parameters": {
                    "type": "object",
                    "properties": {
                         "operation": {
                             "type": "string",
                             "enum": [
                                "launch", "exit",
                                "new_tab", "switch_tab", "list_tabs", "close_tab",
                                "goto", "evaluate", "upload", "dialog_accept", "dialog_dismiss",
                                "wait", "reload", "go_back", "go_forward"
                            ],
                            "description": "Type of browser operation to perform"
                        },
                        "tab_index": {"type": "integer", "description": "Tab index to switch to or close, required for switch_tab/close_tab operations"},
                        "url": {"type": "string", "description": "Target URL to navigate to, required only when operation=goto"},
                        "selector": {"type": "string", "description": "CSS selector, required for wait/upload operations"},
                        "script": {"type": "string", "description": "JavaScript code to execute, required for evaluate operation"},
                        "timeout": {"type": "integer", "description": "Timeout in milliseconds, optional for wait operation, default 5000", "default": 5000},
                        "file_path": {"type": "string", "description": "Local file path to upload, required for upload operation"},
                        "state": {"type": "string", "description": "Wait state: visible, hidden, attached, detached, default visible", "default": "visible"}
                    },
                    "required": ["operation"]
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[Union[str, List[Dict[str, Any]]], str]:
        """Execute browser operation"""
        global _pages, _current_idx
        tool_args = json.loads(arguments)
        operation = tool_args.get("operation")
        
        result: str = ""
        summary: str = ""
        
        try:
            if operation == "launch":
                returncode, _, stderr_text = await self.computer_client.launch_browser()
                if returncode == 0:
                    result = "✅ Browser launched successfully"
                    summary = "✅ Browser launched"
                else:
                    result = f"❌ Browser launch failed: {stderr_text}"
                    summary = "❌ Browser launch failed"
            elif operation == "exit":
                returncode, _, stderr_text = await self.computer_client.exit_browser()
                if returncode == 0:
                    result = "✅ Browser closed successfully"
                    summary = "✅ Browser closed"
                else:
                    result = f"❌ Browser close failed: {stderr_text}"
                    summary = "❌ Browser close failed"
                await self.computer_client.disconnect_browser()
            else:
                browser = await self.computer_client.connect_browser()
                _pages = await self.computer_client.get_browser_pages()
                
                page = get_current_page() if operation not in [
                    "new_tab", "switch_tab", "list_tabs", "close_tab"
                ] else None
                
                if operation == "new_tab":
                    contexts = browser.contexts
                    if not contexts:
                        raise Exception("No browser context available")
                    await contexts[0].new_page()
                    _pages = await self.computer_client.get_browser_pages()
                    _current_idx = len(_pages) - 1
                    result = summary = f"✅ New tab opened successfully, current tab count: {len(_pages)}, active tab index: {_current_idx}."
                elif operation == "switch_tab":
                    tab_index = tool_args["tab_index"]
                    if 0 <= tab_index < len(_pages):
                        _current_idx = tab_index
                        page = _pages[_current_idx]
                        result = summary = f"✅ Switched to tab {tab_index} successfully, current URL: {page.url}, title: {await page.title()}."
                    else:
                        raise Exception(f"Tab index {tab_index} is out of range, total tabs: {len(_pages)}")
                elif operation == "list_tabs":
                    tabs_info = []
                    for i, p in enumerate(_pages):
                        tabs_info.append({
                            "index": i,
                            "active": i == _current_idx,
                            "url": p.url,
                            "title": await p.title()
                        })
                    result = json.dumps({
                        "tabs": tabs_info,
                        "total_tabs": len(_pages),
                        "active_tab_index": _current_idx
                    }, ensure_ascii=False, indent=2)
                    summary = f"✅ Listed {len(_pages)} tabs, active tab index: {_current_idx}."
                elif operation == "close_tab":
                    tab_index = tool_args.get("tab_index", _current_idx)
                    if 0 <= tab_index < len(_pages):
                        if len(_pages) == 1:
                            # If only one tab, create new page before closing to avoid browser closure
                            await browser.contexts[0].new_page()
                        await _pages[tab_index].close()
                        _pages = await self.computer_client.get_browser_pages()
                        if _current_idx >= len(_pages) and len(_pages) > 0:
                            _current_idx = len(_pages) - 1
                        elif len(_pages) == 0:
                            _current_idx = 0
                        result = summary = f"✅ Closed tab {tab_index} successfully, remaining tabs: {len(_pages)}."
                    else:
                        raise Exception(f"Tab index {tab_index} is out of range, total tabs: {len(_pages)}")
                elif operation == "goto":
                    try:
                        await page.goto(tool_args["url"], timeout=5000, wait_until="domcontentloaded")
                    except Exception as e:
                        pass
                    result = summary = f"✅ Browser navigated to {tool_args['url']} successfully, current URL: {page.url}, title: {await page.title()}."
                elif operation == "evaluate":
                    eval_result = await page.evaluate(tool_args["script"])
                    result = f"✅ Browser evaluated JavaScript successfully, result: {eval_result}."
                    summary = f"✅ Browser evaluated JavaScript successfully."
                elif operation == "wait":
                    selector = tool_args.get("selector")
                    if not selector:
                        raise Exception("Selector is required for wait operation")
                    timeout = tool_args.get("timeout", 5000)
                    state = tool_args.get("state", "visible")
                    await page.wait_for_selector(selector, timeout=timeout, state=state)
                    result = summary = f"✅ Waited for element {selector} to be {state} successfully."
                elif operation == "upload":
                    selector = tool_args.get("selector")
                    if not selector:
                        raise Exception("Selector is required for upload operation")
                    file_path = tool_args.get("file_path")
                    if not file_path:
                        raise Exception("file_path is required for upload operation")
                    await page.set_input_files(selector, file_path)
                    result = summary = f"✅ Uploaded file {file_path} to {selector} successfully."
                elif operation == "dialog_accept":
                    page.once("dialog", lambda dialog: dialog.accept())
                    result = summary = f"✅ Dialog accept handler set up, will accept next dialog."
                elif operation == "dialog_dismiss":
                    page.once("dialog", lambda dialog: dialog.dismiss())
                    result = summary = f"✅ Dialog dismiss handler set up, will dismiss next dialog."
                elif operation == "reload":
                    await page.reload(timeout=5000)
                    result = summary = f"✅ Page reloaded successfully, current URL: {page.url}."
                elif operation == "go_back":
                    try:
                        await page.go_back(timeout=3000, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    result = summary = f"✅ Navigated back successfully, current URL: {page.url}."
                elif operation == "go_forward":
                    try:
                        await page.go_forward(timeout=3000, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    result = summary = f"✅ Navigated forward successfully, current URL: {page.url}."
                else:
                    result = summary = f"Browser operation {operation} is not supported."
        except Exception as e:
            await self.computer_client.disconnect_browser()
            result = f"❌ Browser operation {operation} failed: {str(e)}"
            summary = f"❌ Browser operation {operation} failed: {str(e)[:100]}".replace('\n', ' ')

        # Add a small delay to ensure browser operation is completed
        await asyncio.sleep(1)

        return (result, summary)
    