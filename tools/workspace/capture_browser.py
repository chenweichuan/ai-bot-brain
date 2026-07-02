import json
import os
import shlex
import time
from typing import Dict, Any
from common.message import count_text_units
from tools.base import Tool
from providers.computer.client import ComputerClient
from tools.workspace.operate_browser import get_current_page


class CaptureBrowserTool(Tool):
    """Capture page content from browser"""
    
    name = "capture_browser"
    
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
                "description": "Capture current active tab page content from browser, "
                    "supports screenshot, get URL, get text content, extract element info, get cookies, and element screenshot",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["screenshot", "get_url", "get_text", "extract_element", "get_cookies", "screenshot_element"],
                            "description": "Type of capture operation to perform"
                        },
                        "area": {
                            "type": "string",
                            "description": "Capture area, default is 'visible'. Options: 'visible', 'full'",
                            "default": "visible"
                        },
                        "selector": {
                            "type": "string",
                            "description": "CSS selector, required for extract_element/screenshot_element operations"
                        },
                        "extract_fields": {
                            "type": "array",
                            "description": "Fields to extract for extract_element operation: text, attributes, position, visible, html. Default all.",
                            "items": {"type": "string"}
                        },
                        "attributes": {
                            "type": "array",
                            "description": "Specific attributes to extract when extract_fields includes 'attributes'",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["operation"]
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[str, str]:
        """Execute browser capture operation"""
        tool_args = json.loads(arguments)
        operation = tool_args.get("operation")
        area = tool_args.get("area")
        
        try:
            page = get_current_page()

            if operation == "screenshot":
                full_page = area == "full"
                img_bytes = await page.screenshot(full_page=full_page, type="png")
                file_path = os.path.join(self.os_workspace, "browser_screenshot", f"{time.time_ns()}.png")
                prepare_command = f"mkdir -p $(dirname {shlex.quote(file_path)}) && touch {shlex.quote(file_path)} && chmod 777 {shlex.quote(file_path)}"
                await self.computer_client.exec_command(prepare_command)
                with open(file_path, 'wb') as f:
                    f.write(img_bytes)
                result = [
                    {
                        "type": "text",
                        "text": f"Browser screenshot captured successfully, saved to {file_path}, size {len(img_bytes)} bytes."
                    },
                    {
                        "type": "image",
                        "image": {
                            "url": file_path,
                            "detail": "high",
                        },
                    }
                ]
                summary = f"✅ Browser screenshot captured successfully, saved to {file_path}, size {len(img_bytes)} bytes."
            elif operation == "get_url":
                result = summary = f"✅ Browser current URL captured successfully, URL: {page.url}, title: {await page.title()}."
            elif operation == "get_text":
                text = await page.inner_text("body")
                # Truncate content if it exceeds the maximum number of text units
                max_content_units = 30000
                if count_text_units(text) > max_content_units:
                    truncated_length = len(text) / count_text_units(text) * max_content_units * 0.9
                    text = f"{text[:int(truncated_length/2)]}\n...[Content Truncated]...\n{text[-int(truncated_length/2):]}"
                result = f"✅ Browser text content captured successfully, the content is as follows:\n\n{text}"
                summary = "✅ Browser text content captured successfully: " + (text.replace("\n", " ")[:100] + "..." if len(text) > 100 else text).replace("\n", " ")
            elif operation == "extract_element":
                selector = tool_args.get("selector")
                if not selector:
                    raise Exception("Selector is required for extract_element operation")
                
                extract_fields = tool_args.get("extract_fields", ["text", "attributes", "position", "visible", "html"])
                attr_list = tool_args.get("attributes", [])
                
                elements = await page.query_selector_all(selector)
                extracted_data = []
                
                for i, elem in enumerate(elements):
                    elem_data = {"index": i}
                    
                    if "text" in extract_fields:
                        elem_data["text"] = await elem.inner_text()
                    
                    if "html" in extract_fields:
                        elem_data["html"] = await elem.inner_html()
                    
                    if "visible" in extract_fields:
                        elem_data["visible"] = await elem.is_visible()
                    
                    if "position" in extract_fields:
                        bounding_box = await elem.bounding_box()
                        elem_data["position"] = bounding_box if bounding_box else None
                    
                    if "attributes" in extract_fields:
                        if attr_list:
                            attrs = {}
                            for attr in attr_list:
                                value = await elem.get_attribute(attr)
                                if value is not None:
                                    attrs[attr] = value
                            elem_data["attributes"] = attrs
                        else:
                            all_attrs = await page.evaluate('''(element) => {
                                const attrs = {};
                                for (let attr of element.attributes) {
                                    attrs[attr.name] = attr.value;
                                }
                                return attrs;
                            }''', elem)
                            elem_data["attributes"] = all_attrs
                    
                    extracted_data.append(elem_data)
                
                result = json.dumps({
                    "success": True,
                    "selector": selector,
                    "count": len(extracted_data),
                    "elements": extracted_data
                }, ensure_ascii=False, indent=2)
                summary = f"✅ Extracted {len(extracted_data)} elements matching selector '{selector}'"
            elif operation == "get_cookies":
                cookies = await page.context.cookies()
                result = json.dumps({
                    "success": True,
                    "cookies": cookies,
                    "count": len(cookies)
                }, ensure_ascii=False, indent=2)
                summary = f"✅ Got {len(cookies)} cookies from current page"
            elif operation == "screenshot_element":
                selector = tool_args.get("selector")
                if not selector:
                    raise Exception("Selector is required for screenshot_element operation")
                
                element = await page.query_selector(selector)
                if not element:
                    raise Exception(f"Element not found with selector: {selector}")
                
                img_bytes = await element.screenshot(type="png")
                file_path = os.path.join(self.os_workspace, "browser_screenshot", f"element_{time.time_ns()}.png")
                prepare_command = f"mkdir -p $(dirname {shlex.quote(file_path)}) && touch {shlex.quote(file_path)} && chmod 777 {shlex.quote(file_path)}"
                await self.computer_client.exec_command(prepare_command)
                
                with open(file_path, 'wb') as f:
                    f.write(img_bytes)
                
                result = [
                    {
                        "type": "text",
                        "text": f"Element screenshot captured successfully, saved to {file_path}, size {len(img_bytes)} bytes."
                    },
                    {
                        "type": "image",
                        "image": {
                            "url": file_path,
                            "detail": "high",
                        },
                    }
                ]
                summary = f"✅ Element screenshot captured successfully, saved to {file_path}, size {len(img_bytes)} bytes."
            else:
                result = summary = f"Browser capture operation {operation} is not supported."
        except Exception as e:
            await self.computer_client.disconnect_browser()
            result = f"❌ Browser capture {operation} failed: {str(e)}"
            summary = f"❌ Browser capture {operation} failed: {str(e)[:100]}".replace('\n', ' ')
        
        return (result, summary)