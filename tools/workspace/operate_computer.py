import asyncio
import json
import os
import re
import shlex
import time
from typing import Dict, Any, List, Union

from PIL import Image, ImageDraw

from common.message import count_text_units
from tools.base import Tool
from providers.computer.client import ComputerClient


class OperateComputerTool(Tool):
    """Operate computer tool - restricted to configured execution directory"""
    
    name = "operate_computer"
    
    def __init__(self):
        super().__init__()
        self.computer_client = ComputerClient.get_instance()
        self.os_distro = self.computer_client.get_os_distro()
        self.os_user = self.computer_client.get_os_user()
        self.os_workspace = self.computer_client.get_os_workspace()
        self.os_display = self.computer_client.get_os_display()
        self.normalized_coordinates = (1000, 1000)

    async def get_definition(self) -> Dict[str, Any]:
        """Get tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"Operate your computer system running {self.os_distro}. "
                    f"Access is restricted to: your home (default), workspace ({self.os_workspace}) and /tmp directories, and your account ({self.os_user})'s own processes. "
                    "Supports system commands, desktop GUI operations, and waiting to view desktop changes. "
                    "All mouse coordinates are based on the desktop screen size.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "description": "Type of computer operation to perform",
                             "enum": [
                                 "command", "get_mouse_position",
                                 "mouse_click", "mouse_move", "mouse_scroll",
                                 "mouse_drag", "human_like_mouse_drag",
                                 "type_text", "key_press", "key_hold", "key_release",
                                 "window_activate",
                                 "capture_desktop", "wait_for_desktop_changes",
                             ]
                        },
                        "command": {
                            "type": "string",
                            "description": "System command to execute (only for 'command' operation). Must be safe and non-destructive.",
                        },
                        "window_name": {
                            "type": "string",
                            "description": "Window name substring to activate"
                        },
                        "element_desc": {
                            "type": "string",
                            "description": "Natural language description of the GUI element to find"
                        },
                        "element_box": {
                            "type": "array",
                            "description": "Bounding box of target element: [x1, y1, x2, y2] (normalized coordinates, top-left to bottom-right)",
                            "items": {"type": "integer"},
                            "minItems": 4,
                            "maxItems": 4
                        },
                        "end_x": {
                            "type": "integer",
                            "description": "End X coordinate for mouse drag"
                        },
                        "end_y": {
                            "type": "integer",
                            "description": "End Y coordinate for mouse drag"
                        },
                        "button": {
                            "type": "integer",
                            "description": "Mouse button: 1=left, 2=middle, 3=right",
                            "default": 1
                        },
                        "repeat": {
                            "type": "integer",
                            "description": "Number of clicks for mouse_click (default: 1, use 2 for double-click)",
                            "default": 1
                        },
                        "steps": {
                            "type": "integer",
                            "description": "Scroll steps: positive=up, negative=down. Range: [-15, 15], approximately 6 steps ≈ one screen height"
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to type"
                        },
                        "key": {
                            "type": "string",
                            "description": "Key to press/hold/release (e.g. Return, Tab, BackSpace, ctrl+c, F5, Escape)"
                        },
                        "duration": {
                            "type": "number",
                            "description": "Duration in seconds to wait_for_desktop_changes",
                            "default": 3.0
                        }
                    },
                    "required": ["operation"],
                },
            },
        }
    
    async def execute(self, arguments: str) -> tuple[Union[str, List[Dict[str, Any]]], str]:
        """Execute computer operation"""
        tool_args = json.loads(arguments)
        operation = tool_args.get("operation")
        display = self.os_display
        
        final_result = []
        result_text: str = ""
        summary: str = ""
        
        if operation != "command":
            # Get desktop size for gui operation
            desktop_width, desktop_height = await self._get_desktop_size(display)
        
        if operation == "command":
            command = tool_args.get("command")
            preview = command[:50].replace('\n', ' ') + '...' if len(command) > 50 else command.replace('\n', ' ')

            returncode, stdout_text, stderr_text = await self.computer_client.exec_command(command)
            
            # Truncate content if it exceeds the maximum number of text units
            max_content_units = 30000
            if count_text_units(stdout_text) > max_content_units:
                truncated_length = len(stdout_text) / count_text_units(stdout_text) * max_content_units * 0.9
                stdout_text = f"{stdout_text[:int(truncated_length/2)]}\n...[Content Truncated]...\n{stdout_text[-int(truncated_length/2):]}"
            
            # Build result description for LLM
            if returncode == 0:
                if stdout_text:
                    result_text = f"Command executed:\n\n{stdout_text}"
                    summary = f"✅ Command executed successfully: {preview}"
                else:
                    result_text = f"Command executed (no output)"
                    summary = f"✅ Command executed successfully: {preview}"
            elif returncode == -1:
                # Timeout case
                result_parts = []
                result_parts.append(f"Command still running after timeout")
                
                if stdout_text:
                    result_parts.append(f"\nPartial Output:\n{stdout_text}")
                
                if stderr_text:
                    result_parts.append(f"\nError Output:\n{stderr_text}")
                
                result_text = "\n".join(result_parts)
                summary = f"⏱️ Command still running after timeout: {preview}"
            elif returncode == -2:
                # Other exceptions
                result_text = f"Command execution failed: {stderr_text}"
                summary = f"❌ Command execution failed with err {stderr_text[:100]}: {preview}".replace('\n', ' ')
            else:
                # Regular error case
                result_parts = []
                result_parts.append(f"Command failed with exit code {returncode}")
                
                if stdout_text:
                    result_parts.append(f"\nStandard Output:\n{stdout_text}")
                
                if stderr_text:
                    result_parts.append(f"\nStandard Error:\n{stderr_text}")
                
                result_text = "\n".join(result_parts)
                summary = f"❌ Command failed with exit code {returncode}: {preview}"
        
            result_text += f"\n\nNote: The above is the result of your command execution."
        
        elif operation == "mouse_click":
            element_box = tool_args.get("element_box")
            x1, y1, x2, y2 = element_box
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            button = tool_args.get("button", 1)
            repeat = tool_args.get("repeat", 1)
            returncode, stdout_text, stderr_text = await self.computer_client.mouse_click(
                center_x * desktop_width // self.normalized_coordinates[0],
                center_y * desktop_height // self.normalized_coordinates[1],
                button, repeat, display
            )
            if returncode == 0:
                if repeat == 1:
                    result_text = f"Mouse click at element box {element_box} (center: {center_x}, {center_y}) with button {button} succeeded"
                    summary = f"✅ Mouse clicked at element center ({center_x}, {center_y}) with button {button}"
                else:
                    result_text = f"Mouse {repeat}-click at element box {element_box} (center: {center_x}, {center_y}) with button {button} succeeded"
                    summary = f"✅ {repeat}-clicked at element center ({center_x}, {center_y}) with button {button}"
            else:
                result_text = f"Mouse click failed: {stderr_text}"
                summary = f"❌ Mouse click failed at element box {element_box}"
        
        elif operation == "mouse_move":
            element_box = tool_args.get("element_box")
            x1, y1, x2, y2 = element_box
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            returncode, stdout_text, stderr_text = await self.computer_client.mouse_move(
                center_x * desktop_width // self.normalized_coordinates[0],
                center_y * desktop_height // self.normalized_coordinates[1],
                display
            )
            if returncode == 0:
                result_text = f"Mouse moved to element box {element_box} (center: {center_x}, {center_y})"
                summary = f"✅ Mouse moved to element center ({center_x}, {center_y})"
            else:
                result_text = f"Mouse move failed: {stderr_text}"
                summary = f"❌ Mouse move failed to element box {element_box}"
        
        elif operation == "mouse_scroll":
            element_box = tool_args.get("element_box")
            steps = tool_args.get("steps")
            if element_box is not None:
                x1, y1, x2, y2 = element_box
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                await self.computer_client.mouse_move(
                    center_x * desktop_width // self.normalized_coordinates[0],
                    center_y * desktop_height // self.normalized_coordinates[1],
                    display
                )
            returncode, stdout_text, stderr_text = await self.computer_client.mouse_scroll(steps, display)
            await asyncio.sleep(abs(steps) * 0.05)
            direction = "up" if steps > 0 else "down"
            if returncode == 0:
                result_text = f"Mouse scrolled {abs(steps)} steps {direction}"
                summary = f"✅ Scrolled {abs(steps)} steps {direction}"
            else:
                result_text = f"Mouse scroll failed: {stderr_text}"
                summary = f"❌ Mouse scroll failed: {abs(steps)} steps {direction}"
        
        elif operation == "mouse_drag":
            element_box = tool_args.get("element_box")
            x1, y1, x2, y2 = element_box
            start_x = (x1 + x2) // 2
            start_y = (y1 + y2) // 2
            end_x = tool_args.get("end_x")
            end_y = tool_args.get("end_y")
            button = tool_args.get("button", 1)
            returncode, stdout_text, stderr_text = await self.computer_client.mouse_drag(
                start_x * desktop_width // self.normalized_coordinates[0],
                start_y * desktop_height // self.normalized_coordinates[1],
                end_x * desktop_width // self.normalized_coordinates[0],
                end_y * desktop_height // self.normalized_coordinates[1],
                button, display
            )
            if returncode == 0:
                result_text = f"Mouse dragged from element box {element_box} (center: {start_x}, {start_y}) to ({end_x}, {end_y}) with button {button}"
                summary = f"✅ Dragged from element center ({start_x}, {start_y}) to ({end_x}, {end_y})"
            else:
                result_text = f"Mouse drag failed: {stderr_text}"
                summary = f"❌ Mouse drag failed from element box {element_box}"
        
        elif operation == "human_like_mouse_drag":
            element_box = tool_args.get("element_box")
            x1, y1, x2, y2 = element_box
            start_x = (x1 + x2) // 2
            start_y = (y1 + y2) // 2
            end_x = tool_args.get("end_x")
            end_y = tool_args.get("end_y")
            button = tool_args.get("button", 1)
            returncode, stdout_text, stderr_text = await self.computer_client.human_like_mouse_drag(
                start_x * desktop_width // self.normalized_coordinates[0],
                start_y * desktop_height // self.normalized_coordinates[1],
                end_x * desktop_width // self.normalized_coordinates[0],
                end_y * desktop_height // self.normalized_coordinates[1],
                button, display
            )
            if returncode == 0:
                result_text = f"Human-like mouse drag from element box {element_box} (center: {start_x}, {start_y}) to ({end_x}, {end_y}) with button {button}"
                summary = f"✅ Human-like drag from element center ({start_x}, {start_y}) to ({end_x}, {end_y})"
            else:
                result_text = f"Human-like mouse drag failed: {stderr_text}"
                summary = f"❌ Human-like drag failed from element box {element_box}"
        
        elif operation == "type_text":
            text = tool_args.get("text")
            returncode, stdout_text, stderr_text = await self.computer_client.type_text(text, display)
            preview = text[:30] + "..." if len(text) > 30 else text
            if returncode == 0:
                result_text = f"Typed text: {text}"
                summary = f"✅ Typed: {preview}"
            else:
                result_text = f"Type text failed: {stderr_text}"
                summary = f"❌ Type text failed: {preview}"
        
        elif operation == "key_press":
            key = tool_args.get("key")
            returncode, stdout_text, stderr_text = await self.computer_client.key_press(key, display)
            if returncode == 0:
                result_text = f"Pressed key: {key}"
                summary = f"✅ Pressed: {key}"
            else:
                result_text = f"Key press failed: {stderr_text}"
                summary = f"❌ Key press failed: {key}"
        
        elif operation == "key_hold":
            key = tool_args.get("key")
            returncode, stdout_text, stderr_text = await self.computer_client.key_hold(key, display)
            if returncode == 0:
                result_text = f"Holding key: {key} (use key_release to release)"
                summary = f"✅ Holding: {key}"
            else:
                result_text = f"Key hold failed: {stderr_text}"
                summary = f"❌ Key hold failed: {key}"
        
        elif operation == "key_release":
            key = tool_args.get("key")
            returncode, stdout_text, stderr_text = await self.computer_client.key_release(key, display)
            if returncode == 0:
                result_text = f"Released key: {key}"
                summary = f"✅ Released: {key}"
            else:
                result_text = f"Key release failed: {stderr_text}"
                summary = f"❌ Key release failed: {key}"
        
        elif operation == "get_mouse_position":
            returncode, stdout_text, stderr_text = await self.computer_client.get_mouse_position(display)
            if returncode == 0:
                original_x = int(re.search(r"x:(\d+)", stdout_text).group(1))
                original_y = int(re.search(r"y:(\d+)", stdout_text).group(1))
                x = original_x * self.normalized_coordinates[0] // desktop_width
                y = original_y * self.normalized_coordinates[1] // desktop_height
                stdout_text = stdout_text.replace(f"x:{original_x}", f"x:{x}").replace(f"y:{original_y}", f"y:{y}")
                result_text = f"Mouse position: {stdout_text}"
                summary = f"✅ Got mouse position: {stdout_text}"
            else:
                result_text = f"Get mouse position failed: {stderr_text}"
                summary = f"❌ Get mouse position failed"
        
        elif operation == "window_activate":
            window_name = tool_args.get("window_name")
            returncode, stdout_text, stderr_text = await self.computer_client.window_activate(window_name, display)
            if returncode == 0:
                result_text = f"Activated window: {window_name}"
                summary = f"✅ Activated: {window_name}"
            else:
                result_text = f"Window activate failed: {stderr_text}"
                summary = f"❌ Window activate failed: {window_name}"
        
        elif operation == "capture_desktop":
            # Just return a success message, no need to capture screenshot, 
            # as the screenshot will be captured by the unify operation before return
            result_text = "Desktop capture operation completed"
            summary = f"✅ Screenshot saved"
        
        elif operation == "wait_for_desktop_changes":
            duration = tool_args.get("duration", 3.0)
            await asyncio.sleep(duration)
            result_text = f"Waited for {duration} seconds to view the desktop changes"
            summary = f"✅ Waited for {duration} seconds"
        
        else:
            result_text = f"Unknown operation: {operation}"
            summary = f"❌ Unknown operation"
        
        # Assemble the final result
        final_result.append({
            "type": "text",
            "text": result_text,
        })
        
        if operation != "command":
            # Add a small delay to ensure gui operation is completed
            await asyncio.sleep(1)
            # Add screenshot content for gui operation after operation
            with_pointer = operation != "capture_desktop"
            screenshot_content = await self._capture_and_get_screenshot(
                with_pointer=with_pointer, display=display
            )
            final_result.extend(screenshot_content)
        
        return (final_result, summary)
    
    async def _get_desktop_size(self, display: str = None) -> tuple[int, int]:
        """
        Get desktop size
        """
        returncode, stdout_text, stderr_text = await self.computer_client.get_desktop_size(display)
        if returncode == 0:
            desktop_width, desktop_height = map(int, stdout_text.split(" "))
        else:
            raise Exception(f"Get desktop size failed: {stderr_text}")
        
        return (desktop_width, desktop_height)
    
    async def _capture_and_get_screenshot(self, with_pointer: bool = True, display: str = None) -> list[dict[str, any]]:
        """
        Capture desktop and return the screenshot structure with text and image_url
        """
        save_path = os.path.join(self.os_workspace, "desktop_screenshot", f"{time.time_ns()}.png")
        if not os.path.exists(os.path.dirname(save_path)):
            prepare_command = f"mkdir -p $(dirname {shlex.quote(save_path)})"
            await self.computer_client.exec_command(prepare_command)
        
        returncode, _, stderr_text = await self.computer_client.capture_desktop(save_path, display)

        if returncode == 0:
            if with_pointer:
                mouse_x, mouse_y = None, None
                try:
                    # 根据当前鼠标位置，画上鼠标指针的标记
                    _, mouse_position, _ = await self.computer_client.get_mouse_position(display)
                    mouse_x = int(re.search(r"x:(\d+)", mouse_position).group(1))
                    mouse_y = int(re.search(r"y:(\d+)", mouse_position).group(1))
                    # 在图片上画鼠标标记
                    with Image.open(save_path) as img:
                        img_width, _ = img.size
                        draw = ImageDraw.Draw(img)
                        # 箭头形状
                        arrow_points = [
                            (mouse_x, mouse_y),
                            (mouse_x, mouse_y+int(img_width * (8 / self.normalized_coordinates[0]))),
                            (
                                mouse_x+int(img_width * (3 / self.normalized_coordinates[0])),
                                mouse_y+int(img_width * (5 / self.normalized_coordinates[0]))
                            ),
                            (
                                mouse_x+int(img_width * (5 / self.normalized_coordinates[0])),
                                mouse_y+int(img_width * (7 / self.normalized_coordinates[0]))
                            ),
                            (
                                mouse_x+int(img_width * (4 / self.normalized_coordinates[0])),
                                mouse_y+int(img_width * (4 / self.normalized_coordinates[0]))
                            ),
                            (
                                mouse_x+int(img_width * (7 / self.normalized_coordinates[0])),
                                mouse_y+int(img_width * (2 / self.normalized_coordinates[0]))
                            )
                        ]
                        # 红色填充，黑色描边
                        draw.polygon(arrow_points, fill="red", outline="black", width=1)
                        img.save(save_path)
                except Exception as e:
                    print(f"Error getting mouse position: {e}")

            content = [
                {
                    "type": "text",
                    "text": f"Screenshot saved after operation.\n" + \
                        ("The red pointer indicates the current mouse position.\n" if with_pointer else ""),
                },
                {
                    "type": "image",
                    "image": {
                        "url": save_path,
                        "detail": "high",
                    },
                },
            ]
        else:
            content = [
                {
                    "type": "text",
                    "text": f"Screenshot failed: {stderr_text}",
                }
            ]
    
        return content
    
