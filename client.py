"""
Client module for LinkedIn API
"""
import requests
import json
import logging

import linkedin_api.settings as settings

logger = logging.getLogger(__name__)


class Client(object):
    """
    Class to act as a client for the Linkedin API.
    """

    # Settings for general Linkedin API calls
    API_BASE_URL = "https://www.linkedin.com/voyager/api"
    REQUEST_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/66.0.3359.181 Safari/537.36",
        "Accept": "application/vnd.linkedin.normalized+json+2.1",
        "Accept-Language": "en-US,en;q=0.9",
        "x-li-lang": "en_US",
        "x-restli-protocol-version": "2.0.0",
    }

    def __init__(self, debug=False, refresh_cookies=False, skip_cookie_load=False):
        """
        Initialize the client.
        
        Args:
            debug: Enable debug logging
            refresh_cookies: Force refresh cookies from file
            skip_cookie_load: Skip loading cookies from JSON file (useful when injecting cookies manually)
        """
        self.session = requests.session()
        self.session.headers.update(Client.REQUEST_HEADERS)

        self.logger = logger
        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
        
        # Load cookies from JSON file (unless skipped)
        if not skip_cookie_load:
            self._load_cookies_from_json()

    def _load_cookies_from_json(self):
        """
        Load cookies from the JSON file and set them in the session.
        """
        try:
            with open(settings.COOKIE_FILE_PATH, "r") as f:
                cookies_data = json.load(f)
                
            # Convert JSON cookies to requests cookie jar
            for cookie in cookies_data:
                self.session.cookies.set(
                    name=cookie["name"],
                    value=cookie["value"],
                    domain=cookie.get("domain", ""),
                    path=cookie.get("path", "/"),
                )
            
            # Set CSRF token from JSESSIONID
            if "JSESSIONID" in self.session.cookies:
                csrf_token = self.session.cookies["JSESSIONID"].strip('"')
                self.session.headers["csrf-token"] = csrf_token
                self.logger.debug(f"CSRF token set: {csrf_token}")
            
            self.logger.info("Cookies loaded successfully from JSON file")
            
        except FileNotFoundError:
            self.logger.warning(f"Cookie file not found at {settings.COOKIE_FILE_PATH}. Skipping cookie load.")
            # Don't raise - allow initialization without cookies file
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse cookie JSON file: {e}")
            # Don't raise - allow initialization to continue
        except Exception as e:
            self.logger.error(f"Error loading cookies: {e}")
            # Don't raise - allow initialization to continue

    def refresh_cookies(self):
        """
        Reload cookies from the JSON file.
        """
        self._load_cookies_from_json()
