"""
Provides linkedin api-related code
"""
import random
import logging
from time import sleep

from linkedin_api.client import Client

logger = logging.getLogger(__name__)


class Linkedin(object):
    """
    Class for accessing Linkedin API.
    """

    _MAX_SEARCH_COUNT = 49  # max seems to be 49
    _MAX_REPEATED_REQUESTS = 200  # VERY conservative max requests count to avoid rate-limit

    def __init__(self, debug=False, refresh_cookies=False, skip_cookie_load=False):
        """
        Initialize the LinkedIn API client.
        
        Args:
            debug: Enable debug logging
            refresh_cookies: Force refresh cookies from file
            skip_cookie_load: Skip loading cookies from JSON file (useful when injecting cookies manually)
        """
        self.client = Client(debug=debug, refresh_cookies=refresh_cookies, skip_cookie_load=skip_cookie_load)
        self.logger = logger

    def get_user_profile(self):
        """
        Get the current authenticated user's profile.
        
        Returns:
            Dictionary with user profile data
        """
        res = self.client.session.get(f"{self.client.API_BASE_URL}/me")
        
        if res.status_code != 200:
            self.logger.error(f"Failed to get user profile: {res.status_code} - {res.text}")
            return {}
        
        data = res.json()
        
        # Extract user info from response
        user_data = data.get("data", {})
        included = data.get("included", [])
        
        # Find mini profile in included
        mini_profile = None
        for item in included:
            if item.get("$type") == "com.linkedin.voyager.identity.shared.MiniProfile":
                mini_profile = item
                break
        
        result = {
            "plain_id": user_data.get("plainId"),
            "premium_subscriber": user_data.get("premiumSubscriber"),
        }
        
        if mini_profile:
            result.update({
                "first_name": mini_profile.get("firstName"),
                "last_name": mini_profile.get("lastName"),
                "occupation": mini_profile.get("occupation"),
                "public_identifier": mini_profile.get("publicIdentifier"),
                "entity_urn": mini_profile.get("entityUrn"),
                "object_urn": mini_profile.get("objectUrn"),
            })
        
        return result

    def create_post(self, text, visibility="ANYONE"):
        """
        Create a post on LinkedIn feed.
        
        Args:
            text (str): The text content of the post
            visibility (str): Post visibility - "ANYONE" (public), "CONNECTIONS" (connections only)
        
        Returns:
            dict: Response data from the API including post URL and status
        """
        # Endpoint discovered from browser network inspection
        # The queryId is part of the URL, not the payload
        endpoint = f"{self.client.API_BASE_URL}/graphql"
        
        params = {
            "action": "execute",
            "queryId": "voyagerContentcreationDashShares.279996efa5064c01775d5aff003d9377"
        }
        
        payload = {
            "variables": {
                "post": {
                    "allowedCommentersScope": "ALL",
                    "intendedShareLifeCycleState": "PUBLISHED",
                    "origin": "FEED",
                    "visibilityDataUnion": {
                        "visibilityType": visibility
                    },
                    "commentary": {
                        "text": text,
                        "attributesV2": []
                    }
                }
            },
            "queryId": "voyagerContentcreationDashShares.279996efa5064c01775d5aff003d9377"
        }
        
        # Required headers based on successful browser request
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
        }
        
        try:
            res = self.client.session.post(
                endpoint,
                params=params,
                json=payload,
                headers=headers
            )
            
            if res.status_code == 200:
                data = res.json()
                self.logger.info("✅ Post created successfully!")
                
                # Extract useful information from response
                result = {
                    "success": True,
                    "status_code": res.status_code,
                    "data": data
                }
                
                # Try to extract the post URL from the response
                try:
                    if "included" in data:
                        for item in data["included"]:
                            if item.get("$type") == "com.linkedin.voyager.dash.social.SocialContent":
                                result["post_url"] = item.get("shareUrl")
                                break
                            elif item.get("$type") == "com.linkedin.voyager.dash.feed.Update":
                                social_content = item.get("socialContent", {})
                                result["post_url"] = social_content.get("shareUrl")
                                break
                except Exception as e:
                    self.logger.debug(f"Could not extract post URL: {e}")
                
                return result
            else:
                self.logger.error(f"Failed to create post: {res.status_code} - {res.text[:500]}")
                return {
                    "success": False,
                    "error": res.text,
                    "status_code": res.status_code
                }
                
        except Exception as e:
            self.logger.error(f"Error creating post: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def search(self, params, max_results=None, results=[]):
        """
        Do a search.
        """
        sleep(random.randint(0, 1))  # sleep a random duration to try and evade suspention

        count = max_results if max_results and max_results <= Linkedin._MAX_SEARCH_COUNT else Linkedin._MAX_SEARCH_COUNT
        default_params = {
            "count": count,
            "guides": "List()",
            "origin": "GLOBAL_SEARCH_HEADER",
            "q": "guided",
            "start": len(results),
        }

        default_params.update(params)

        res = self.client.session.get(
            f"{self.client.API_BASE_URL}/search/cluster", params=default_params)
        data = res.json()

        total_found = data.get("paging", {}).get("total")
        if total_found == 0 or total_found is None:
            self.logger.debug("found none...")
            return []

        # recursive base case
        if (
            len(data["elements"]) == 0
            or (max_results is not None and len(results) >= max_results)
            or len(results) >= total_found
            or len(results) / max_results >= Linkedin._MAX_REPEATED_REQUESTS
        ):
            return results

        results.extend(data["elements"][0]["elements"])
        self.logger.debug(f"results grew: {len(results)}")

        return self.search(params, results=results, max_results=max_results)

    def search_people(self, keywords=None, connection_of=None, network_depth=None, regions=None, industries=None, max_results=None):
        """
        Do a people search.
        
        Args:
            keywords: Search keywords
            connection_of: Connection of a specific profile URN
            network_depth: Network depth (e.g., "F" for 1st connections)
            regions: List of region codes
            industries: List of industry codes
            max_results: Maximum number of results to return
        """
        guides = ["v->PEOPLE"]
        if connection_of:
            guides.append(f"facetConnectionOf->{connection_of}")
        if network_depth:
            guides.append(f"facetNetwork->{network_depth}")
        if regions:
            guides.append(f'facetGeoRegion->{"|".join(regions)}')
        if industries:
            guides.append(f'facetIndustry->{"|".join(industries)}')

        params = {"guides": "List({})".format(",".join(guides))}

        if keywords:
            params["keywords"] = keywords

        data = self.search(params, max_results=max_results)

        results = []
        for item in data:
            search_profile = item["hitInfo"]["com.linkedin.voyager.search.SearchProfile"]
            profile_id = search_profile["id"]
            distance = search_profile["distance"]["value"]

            results.append(
                {
                    "urn_id": profile_id,
                    "distance": distance,
                    "public_id": search_profile["miniProfile"]["publicIdentifier"],
                }
            )

        return results

    def get_profile_contact_info(self, public_id=None, urn_id=None):
        """
        Return data for a single profile's contact info.

        Args:
            public_id: public identifier i.e. tom-quirk-1928345
            urn_id: id provided by the related URN
        """
        res = self.client.session.get(
            f"{self.client.API_BASE_URL}/identity/profiles/{public_id or urn_id}/profileContactInfo"
        )
        data = res.json()

        contact_info = {
            "email_address": data.get("emailAddress"),
            "websites": [],
            "phone_numbers": data.get("phoneNumbers", []),
        }

        websites = data.get("websites", [])
        for item in websites:
            if "com.linkedin.voyager.identity.profile.StandardWebsite" in item["type"]:
                item["label"] = item["type"]["com.linkedin.voyager.identity.profile.StandardWebsite"]["category"]
            elif "com.linkedin.voyager.identity.profile.CustomWebsite" in item["type"]:
                item["label"] = item["type"]["com.linkedin.voyager.identity.profile.CustomWebsite"]["label"]

            del item["type"]

        contact_info["websites"] = websites

        return contact_info

    def get_profile(self, public_id=None, urn_id=None):
        """
        Return data for a single profile.

        Args:
            public_id: public identifier i.e. tom-quirk-1928345
            urn_id: id provided by the related URN
            
        Note: This endpoint may return 410 (Gone) errors as LinkedIn has deprecated it.
        Consider using get_user_profile() for the current user instead.
        """
        sleep(random.randint(2, 5))  # sleep a random duration to try and evade suspention
        res = self.client.session.get(
            f"{self.client.API_BASE_URL}/identity/profiles/{public_id or urn_id}/profileView"
        )

        if res.status_code != 200:
            self.logger.warning(f"Profile request failed with status {res.status_code}: {res.text}")
            return {}

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: {}".format(data.get("message", "Unknown error")))
            return {}

        if "profile" not in data:
            self.logger.warning("No profile data in response")
            return {}

        # massage [profile] data
        profile = data["profile"]
        if "miniProfile" in profile:
            if "picture" in profile["miniProfile"]:
                profile["displayPictureUrl"] = profile["miniProfile"]["picture"]["com.linkedin.common.VectorImage"][
                    "rootUrl"
                ]
            profile["profile_id"] = profile["miniProfile"]["entityUrn"].split(":")[3]

            del profile["miniProfile"]

        del profile["defaultLocale"]
        del profile["supportedLocales"]
        del profile["versionTag"]
        del profile["showEducationOnProfileTopCard"]

        # massage [experience] data
        experience = data["positionView"]["elements"]
        for item in experience:
            if "company" in item and "miniCompany" in item["company"]:
                if "logo" in item["company"]["miniCompany"]:
                    logo = item["company"]["miniCompany"]["logo"].get(
                        "com.linkedin.common.VectorImage")
                    if logo:
                        item["companyLogoUrl"] = logo["rootUrl"]
                del item["company"]["miniCompany"]

        profile["experience"] = experience

        # massage [skills] data
        skills = [item["name"] for item in data["skillView"]["elements"]]

        profile["skills"] = skills

        # massage [education] data
        education = data["educationView"]["elements"]
        for item in education:
            if "school" in item:
                if "logo" in item["school"]:
                    item["school"]["logoUrl"] = item["school"]["logo"]["com.linkedin.common.VectorImage"]["rootUrl"]
                    del item["school"]["logo"]

        profile["education"] = education

        return profile

    def get_profile_connections(self, urn_id, max_results=None):
        """
        Return a list of profile ids connected to profile of given [urn_id]
        
        Args:
            urn_id: URN ID of the profile
            max_results: Maximum number of connections to return
        """
        return self.search_people(connection_of=urn_id, network_depth="F", max_results=max_results)

    def get_school(self, public_id):
        """
        Return data for a single school.

        Args:
            public_id: public identifier i.e. uq
        """
        sleep(random.randint(2, 5))  # sleep a random duration to try and evade suspention
        params = {
            "decoration": (
                """
                (
                autoGenerated,backgroundCoverImage,
                companyEmployeesSearchPageUrl,companyPageUrl,confirmedLocations*,coverPhoto,dataVersion,description,
                entityUrn,followingInfo,foundedOn,headquarter,jobSearchPageUrl,lcpTreatment,logo,name,type,overviewPhoto,
                paidCompany,partnerCompanyUrl,partnerLogo,partnerLogoImage,rankForTopCompanies,salesNavigatorCompanyUrl,
                school,showcase,staffCount,staffCountRange,staffingCompany,topCompaniesListName,universalName,url,
                companyIndustries*,industries,specialities,
                acquirerCompany~(entityUrn,logo,name,industries,followingInfo,url,paidCompany,universalName),
                affiliatedCompanies*~(entityUrn,logo,name,industries,followingInfo,url,paidCompany,universalName),
                groups*~(entityUrn,largeLogo,groupName,memberCount,websiteUrl,url),
                showcasePages*~(entityUrn,logo,name,industries,followingInfo,url,description,universalName)
                )
                """
            ),
            "q": "universalName",
            "universalName": public_id
        }

        res = self.client.session.get(
            f"{self.client.API_BASE_URL}/organization/companies",
            params=params
        )

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: {}".format(data["message"]))
            return {}

        school = data["elements"][0]

        return school

    def get_company(self, public_id):
        """
        Return data for a single company.

        Args:
            public_id: public identifier i.e. univeristy-of-queensland
        """
        sleep(random.randint(2, 5))  # sleep a random duration to try and evade suspention
        params = {
            "decoration": (
                """
                (
                affiliatedCompaniesWithEmployeesRollup,affiliatedCompaniesWithJobsRollup,articlePermalinkForTopCompanies,
                autoGenerated,backgroundCoverImage,companyEmployeesSearchPageUrl,
                companyPageUrl,confirmedLocations*,coverPhoto,dataVersion,description,entityUrn,followingInfo,
                foundedOn,headquarter,jobSearchPageUrl,lcpTreatment,logo,name,type,overviewPhoto,paidCompany,
                partnerCompanyUrl,partnerLogo,partnerLogoImage,permissions,rankForTopCompanies,
                salesNavigatorCompanyUrl,school,showcase,staffCount,staffCountRange,staffingCompany,
                topCompaniesListName,universalName,url,companyIndustries*,industries,specialities,
                acquirerCompany~(entityUrn,logo,name,industries,followingInfo,url,paidCompany,universalName),
                affiliatedCompanies*~(entityUrn,logo,name,industries,followingInfo,url,paidCompany,universalName),
                groups*~(entityUrn,largeLogo,groupName,memberCount,websiteUrl,url),
                showcasePages*~(entityUrn,logo,name,industries,followingInfo,url,description,universalName)
                )
                """
            ),
            "q": "universalName",
            "universalName": public_id
        }

        res = self.client.session.get(
            f"{self.client.API_BASE_URL}/organization/companies",
            params=params
        )

        data = res.json()

        if data and "status" in data and data["status"] != 200:
            self.logger.info("request failed: {}".format(data["message"]))
            return {}

        company = data["elements"][0]

        return company
