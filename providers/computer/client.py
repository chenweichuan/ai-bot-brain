"""
Computer operation module - client implementation
"""
import asyncio
import shlex
import subprocess
import distro
import base64
from typing import Optional, List
from common.log import logger
from config import conf
from playwright.async_api import Browser, Page, async_playwright


class ComputerClient:
    """Client for computer operation - singleton implementation"""
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self.os_distro = f"{distro.name()} {distro.version()}"
        self.os_user = conf().get("bot_os_user", "bot")
        self.os_workspace = conf().get("bot_os_workspace", "/opt/bot")
        self.os_display = conf().get("os_display", ":1")
        
        # Browser management
        self._browser: Optional[Browser] = None
        self._pw = None
        self._cdp_url = "http://localhost:9222"
    
    def get_os_distro(self):
        """Get os distro"""
        return self.os_distro

    def get_os_user(self):
        """Get os user"""
        return self.os_user
    
    def get_os_workspace(self):
        """Get os data"""
        return self.os_workspace

    def get_os_display(self):
        """Get os display"""
        return self.os_display

    async def exec_command(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """
        Execute os command in non-login shell with timeout
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (default: 60)
            
        Returns:
            Tuple of (returncode, stdout_text, stderr_text)
            - returncode >= 0: Command completed with this exit code
            - returncode = -1: Command timed out but is still running
            - returncode = -2: Other exception occurred
        """

        try:
            # Use base64 encoding to avoid shell escape issues with special characters/newlines
            encoded_command = base64.b64encode(command.encode()).decode()
            
            # Ultimate concise way: here-string with base64 decoding
            # No echo needed, use bash here-string directly
            process = await asyncio.create_subprocess_exec(
                "sudo", "-u", self.os_user, "-i",
                "/bin/bash", "-c", f"base64 -d <<< '{encoded_command}' | /bin/bash",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            
            # Create a task for process.communicate()
            communicate_task = asyncio.create_task(process.communicate())
            
            try:
                # Wait with timeout
                done, _ = await asyncio.wait(
                    [communicate_task],
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                if communicate_task in done:
                    stdout, stderr = communicate_task.result()
                    
                    stdout_text = stdout.decode('utf-8', errors='replace').strip()
                    stderr_text = stderr.decode('utf-8', errors='replace').strip()
                    
                    if process.returncode == 0:
                        logger.info(f"[SystemCommandClient] Command executed successfully as {self.os_user} user: {command}")
                    else:
                        logger.error(f"[SystemCommandClient] Command failed with exit code {process.returncode} as {self.os_user} user: {command}")
                        
                    return (process.returncode, stdout_text, stderr_text)
                else:
                    # Timeout occurred - cancel the communicate task
                    communicate_task.cancel()
                    
                    # Try to get whatever output we can by reading directly
                    stdout_data = b""
                    stderr_data = b""
                    
                    # Read stdout if available
                    if process.stdout and not process.stdout.at_eof():
                        try:
                            # Read with a small timeout
                            stdout_data = await asyncio.wait_for(
                                process.stdout.read(n=1024*1024),
                                timeout=0.1
                            )
                        except:
                            pass
                    
                    # Read stderr if available
                    if process.stderr and not process.stderr.at_eof():
                        try:
                            # Read with a small timeout
                            stderr_data = await asyncio.wait_for(
                                process.stderr.read(n=1024*1024),
                                timeout=0.1
                            )
                        except:
                            pass
                    
                    stdout_text = stdout_data.decode('utf-8', errors='replace').strip()
                    stderr_text = stderr_data.decode('utf-8', errors='replace').strip()
                    
                    logger.warning(f"[SystemCommandClient] Command timed out after {timeout} seconds but is still running as {self.os_user} user: {command}")
                    
                    # Return special return code -1 for timeout (command still running)
                    return (-1, stdout_text, stderr_text)
                    
            except asyncio.CancelledError:
                # This shouldn't happen normally, but just in case
                communicate_task.cancel()
                raise
                
        except Exception as e:
            # Unified exception handling for all errors
            error_message = f"Command execution failed: {type(e).__name__}\n"
            error_message += f"User: {self.os_user}\n"
            error_message += f"Command: {command}\n"
            error_message += f"Error: {str(e)}"
            logger.error(f"[SystemCommandClient] {error_message}")
            logger.exception(e)
            
            # Return special return code -2 for other exceptions
            return (-2, "", str(e))

    # ========== Extended desktop GUI automation operations ==========
    
    async def get_desktop_size(self, display: str = None) -> tuple[int, str, str]:
        """Get desktop size in pixels, returns raw output directly"""
        cmd = "xdotool getdisplaygeometry"
        return await self.exec_desktop(cmd, 10, display)

    async def exec_desktop(self, command: str, timeout: int = 30, display: str = None) -> tuple[int, str, str]:
        """Execute command on desktop display, wrapper for convenience"""
        if display is None:
            display = self.os_display
        desktop_command = f"export DISPLAY={display}; {command}"
        return await self.exec_command(desktop_command, timeout)

    async def mouse_click(self, x: int, y: int, button: int = 1, repeat: int = 1, display: str = None) -> tuple[int, str, str]:
        """Simulate mouse click at (x,y) coordinates: 1=left 2=middle 3=right, repeat=1 for single-click, repeat=2 for double-click"""
        if not x or not y:
            return (-3, "", "x and y coordinates are required")
        
        if repeat <= 1:
            cmd = f"xdotool mousemove {x} {y} click {button}"
        else:
            cmd = f"xdotool mousemove {x} {y} click --repeat {repeat} {button}"
        return await self.exec_desktop(cmd, 10, display)

    async def mouse_move(self, x: int, y: int, display: str = None) -> tuple[int, str, str]:
        """Move mouse to (x,y) coordinates"""
        if not x or not y:
            return (-3, "", "x and y coordinates are required")
        
        cmd = f"xdotool mousemove {x} {y}"
        return await self.exec_desktop(cmd, 10, display)

    async def mouse_scroll(self, steps: int, display: str = None) -> tuple[int, str, str]:
        """
        Scroll mouse wheel: positive = scroll up, negative = scroll down
        steps: number of scroll steps, e.g. 5=up 5 steps, -3=down 3 steps
        """
        button = 4 if steps > 0 else 5
        repeat = abs(steps)
        cmd = f"xdotool click --repeat {repeat} {button}"
        return await self.exec_desktop(cmd, 10, display)

    async def mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: int = 1, display: str = None) -> tuple[int, str, str]:
        """Drag from (start_x,start_y) to (end_x,end_y) with given button (default left)"""
        cmd = f"xdotool mousemove {start_x} {start_y} mousedown {button} mousemove {end_x} {end_y} mouseup {button}"
        return await self.exec_desktop(cmd, 15, display)

    async def human_like_mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, button: int = 1, display: str = None) -> tuple[int, str, str]:
        """
        Human-like mouse drag with jitter, acceleration/deceleration, and random delays
        
        Args:
            start_x: Starting X coordinate
            start_y: Starting Y coordinate
            end_x: Ending X coordinate
            end_y: Ending Y coordinate
            button: Mouse button (1=left, 2=middle, 3=right)
            display: Display to use (default: self.os_display)
            
        Returns:
            Tuple of (returncode, stdout_text, stderr_text)
        """
        import random
        
        if not start_x or not start_y or not end_x or not end_y:
            return (-3, "", "start_x, start_y, end_x, end_y coordinates are required")
        
        # Step 1: Add small jitter to start point
        jitter_x = random.randint(-2, 2)  # -2 ~ +2 pixels
        jitter_y = random.randint(-1, 1)  # -1 ~ +1 pixels
        actual_start_x = start_x + jitter_x
        actual_start_y = start_y + jitter_y
        
        # Step 2: Move to start point with reaction delay
        result = await self.mouse_move(actual_start_x, actual_start_y, display)
        if result[0] != 0:
            return result
        await asyncio.sleep(random.uniform(0.2, 0.6))  # 200-600ms reaction time
        
        # Step 3: Press mouse button
        cmd = f"xdotool mousedown {button}"
        result = await self.exec_desktop(cmd, 10, display)
        if result[0] != 0:
            return result
        await asyncio.sleep(random.uniform(0.05, 0.15))  # 50-150ms delay after press
        
        # Step 4: Split drag into steps with acceleration/deceleration
        steps = random.randint(10, 15)
        
        # Use floating point to avoid integer division precision loss
        dx = (end_x - actual_start_x) / steps
        dy = (end_y - actual_start_y) / steps
        
        for i in range(1, steps + 1):
            # Calculate position with small random jitter per step
            step_jitter_x = random.randint(-1, 1)
            step_jitter_y = random.randint(-1, 1)
            current_x = int(actual_start_x + dx * i + step_jitter_x)
            current_y = int(actual_start_y + dy * i + step_jitter_y)
            
            result = await self.mouse_move(current_x, current_y, display)
            if result[0] != 0:
                return result
            
            # Random delay per step (10-40ms)
            await asyncio.sleep(random.uniform(0.01, 0.04))
        
        # Step 5: Final position with small jitter (not perfectly aligned)
        final_jitter_x = random.randint(-2, 1)  # -2 ~ +1 pixels
        final_jitter_y = random.randint(-1, 1)  # -1 ~ +1 pixels
        final_x = end_x + final_jitter_x
        final_y = end_y + final_jitter_y
        
        result = await self.mouse_move(final_x, final_y, display)
        if result[0] != 0:
            return result
        
        # Step 6: Release mouse button
        await asyncio.sleep(random.uniform(0.05, 0.2))  # 50-200ms delay before release
        cmd = f"xdotool mouseup {button}"
        return await self.exec_desktop(cmd, 10, display)

    async def type_text(self, text: str, display: str = None) -> tuple[int, str, str]:
        """Type text as keyboard input to focused window"""
        # 如果包含换行符或者非ASCII字符，统一使用剪贴板方式
        # 因为 xdotool type 不能很好地处理换行符和特殊字符
        if all(ord(c) < 128 for c in text) and '\n' not in text and '\r' not in text:
            cmd = f"xdotool type {shlex.quote(text)}"
        else:
            cmd = f"printf '%s' {shlex.quote(text)} | xclip -selection clipboard > /dev/null 2>&1 && xdotool key ctrl+v"
        return await self.exec_desktop(cmd, 10, display)

    async def key_press(self, key: str, display: str = None) -> tuple[int, str, str]:
        """Press single keyboard key (e.g. Return, Tab, BackSpace, ctrl+c, F5, Escape)"""
        cmd = f"xdotool key {key}"
        return await self.exec_desktop(cmd, 10, display)

    async def key_hold(self, key: str, display: str = None) -> tuple[int, str, str]:
        """Press and hold a key (needs key_release to release)"""
        cmd = f"xdotool keydown {key}"
        return await self.exec_desktop(cmd, 10, display)

    async def key_release(self, key: str, display: str = None) -> tuple[int, str, str]:
        """Release a held key"""
        cmd = f"xdotool keyup {key}"
        return await self.exec_desktop(cmd, 10, display)

    async def get_mouse_position(self, display: str = None) -> tuple[int, str, str]:
        """Get current mouse position, returns raw output directly"""
        cmd = "xdotool getmouselocation"
        return await self.exec_desktop(cmd, 10, display)

    async def window_activate(self, window_name: str, display: str = None) -> tuple[int, str, str]:
        """Activate and focus window by name substring (e.g. 'chromium' activates browser)"""
        cmd = f"xdotool search --name \"{window_name}\" windowactivate"
        return await self.exec_desktop(cmd, 10, display)

    async def capture_desktop(self, save_path: str = "/tmp/desktop_screenshot.png", display: str = None) -> tuple[int, str, str]:
        """Capture full desktop screenshot to file using ImageMagick import"""
        cmd = f"import -window root {save_path}"
        return await self.exec_desktop(cmd, 10, display)

    # ========== Browser management via CDP ==========

    async def launch_browser(self, display: str = None) -> tuple[int, str, str]:
        """Launch browser with remote debugging port"""
        cmd = f"/usr/bin/chromium-browser --remote-debugging-port=9222 " \
            "--user-data-dir=/opt/witron/chromium_data " \
            "--start-maximized " \
            "--test-type " \
            "--no-first-run " \
            "--no-default-browser-check " \
            "--no-sandbox " \
            "--disable-gpu " \
            "--disable-software-rasterizer " \
            "--disable-dev-shm-usage " \
            "--disable-extensions " \
            "--mute-audio " \
            "--disable-background-timer-throttling " \
            "--disable-backgrounding-occluded-windows " \
            "--disable-renderer-backgrounding " \
            "> /dev/null 2>&1 &"
        result = await self.exec_desktop(cmd, 10, display)
        await asyncio.sleep(3)
        return result

    async def exit_browser(self, display: str = None) -> tuple[int, str, str]:
        """Close browser process"""
        cmd = f"pkill -f 'remote-debugging-port=9222'"
        result = await self.exec_desktop(cmd, 10, display)
        await asyncio.sleep(1)
        return result

    async def connect_browser(self) -> Browser:
        """Connect to browser via CDP, returns browser instance"""
        try:
            # Check if browser connection is still valid
            if self._browser:
                # Use is_connected() method to verify connection status
                if self._browser.is_connected():
                    return self._browser
                logger.warning(f"[ComputerClient] Browser connection closed, reconnecting...")
                self._browser = None
        except Exception:
            logger.warning(f"[ComputerClient] Browser connection invalid, reconnecting...")
            self._browser = None

        # Create new connection
        if not self._pw:
            self._pw = await async_playwright().start()
        
        logger.info(f"[ComputerClient] Connecting to browser via CDP: {self._cdp_url}")
        self._browser = await self._pw.chromium.connect_over_cdp(self._cdp_url)
        logger.info(f"[ComputerClient] Browser connected successfully")
        return self._browser

    async def disconnect_browser(self):
        """Disconnect from browser (keeps browser pages intact)"""
        if self._browser:
            # 只清空浏览器引用，保持浏览器页面完整
            self._browser = None

    async def get_browser_pages(self) -> List[Page]:
        """Get all opened browser pages"""
        browser = await self.connect_browser()
        contexts = browser.contexts
        if not contexts:
            return []
        pages = contexts[0].pages
        if not pages:
            new_page = await contexts[0].new_page()
            return [new_page]
        return pages
