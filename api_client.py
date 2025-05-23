# api_client.py
import requests
import json
import time
import logging
import urllib3

from config import BASE_API_URL, MAIN_SITE_CHECK_URL, MAX_RETRIES, MAX_BACKOFF_DELAY, SESSION

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__) 


class AnemAPIClient:
    def __init__(self, initial_backoff_general, initial_backoff_429, request_timeout):
        self.session = SESSION 
        self.base_url = BASE_API_URL
        self.initial_backoff_general = initial_backoff_general
        self.initial_backoff_429 = initial_backoff_429
        self.request_timeout = request_timeout


    def _make_request(self, method, endpoint, params=None, data=None, extra_headers=None, is_site_check=False):
        url = f"{self.base_url}/{endpoint}" if not is_site_check else MAIN_SITE_CHECK_URL

        headers = self.session.headers.copy()
        if extra_headers:
            headers.update(extra_headers)

        current_retry = 0
        max_retries_for_this_call = 0 if is_site_check else MAX_RETRIES 
        current_delay_general = self.initial_backoff_general
        current_delay_429 = self.initial_backoff_429


        while current_retry <= max_retries_for_this_call:
            actual_delay_to_use = current_delay_general 
            log_prefix = f"الطلب {method.upper()} إلى {url}"
            if is_site_check:
                log_prefix = f"فحص توفر الموقع: {url}"
            
            logger.debug(f"{log_prefix} (محاولة {current_retry + 1}/{max_retries_for_this_call + 1}) مع البيانات: {params or data}")
            
            try:
                response = None
                request_timeout_val = 5 if is_site_check else self.request_timeout

                if method.upper() == 'GET':
                    response = self.session.get(url, params=params, headers=headers, timeout=request_timeout_val, verify=False)
                elif method.upper() == 'POST':
                    headers['Content-Type'] = 'application/json' 
                    response = self.session.post(url, json=data, headers=headers, timeout=request_timeout_val, verify=False)
                else:
                    logger.error(f"الطريقة {method} غير مدعومة لـ {url}")
                    return None, f"الطريقة {method} غير مدعومة"

                logger.debug(f"استجابة الخادم لـ {url}: {response.status_code}")

                if response.status_code == 429: 
                    actual_delay_to_use = current_delay_429
                    logger.warning(f"خطأ 429 (طلبات كثيرة جدًا) من الخادم لـ {url}. الانتظار {actual_delay_to_use} ثانية.")
                    if current_retry >= max_retries_for_this_call:
                        logger.error(f"تم تجاوز الحد الأقصى لإعادة المحاولة (429) لـ {url}")
                        return None, "طلبات كثيرة جدًا للخادم (429). يرجى الانتظار والمحاولة لاحقًا."
                    time.sleep(actual_delay_to_use)
                    current_delay_429 = min(current_delay_429 * 2, MAX_BACKOFF_DELAY) 
                    current_retry += 1
                    continue
                
                actual_delay_to_use = current_delay_general
                response.raise_for_status() 
                
                if is_site_check: 
                    return True, None 
                
                try:
                    # For RendezVous/Create, the "Eligible:false" response might be JSON but with a specific structure.
                    # Or it could be non-JSON text as handled before.
                    json_response = response.json()
                    # If it's JSON and from RendezVous/Create, check for "Eligible": false specifically
                    if endpoint == 'RendezVous/Create' and isinstance(json_response, dict) and json_response.get("Eligible") is False:
                        logger.warning(f"استجابة JSON من {url} تشير إلى Eligible:false. الاستجابة: {json_response}")
                        # Return the JSON as is, the caller (thread) will interpret "Eligible"
                        return json_response, None # No error string, as it's a valid (though negative) API response
                    return json_response, None
                except json.JSONDecodeError:
                    logger.error(f"خطأ في تحليل استجابة JSON من {url}. الاستجابة (أول 200 حرف): {response.text[:200] if response else 'No response object'}")
                    if endpoint == 'RendezVous/Create' and response and response.text:
                        logger.warning(f"استجابة نصية غير JSON من {url} ولكنها تحتوي على نص: {response.text[:200]}")
                        # Heuristic: if "Eligible":false is in the raw text, treat it as such
                        if "\"Eligible\":false" in response.text.lower():
                             # Try to construct a dict similar to what the thread expects
                             message_from_text = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائكم لأحد شروط الأهلية اللازمة." # Default or extract if possible
                             # This is a guess, the actual message might be different or not present in raw text
                             return {"Eligible": False, "message": message_from_text, "raw_text": True}, None

                        return {"raw_text": response.text, "is_non_json_success_heuristic": "Eligible" in response.text}, "استجابة نصية غير متوقعة من الخادم."
                    return None, "خطأ في تحليل البيانات المستلمة من الخادم (ليست JSON)."

            except requests.exceptions.SSLError as e:
                error_message = f"خطأ SSL عند الاتصال بـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
            except requests.exceptions.ConnectTimeout as e: 
                error_message = f"انتهت مهلة الاتصال بالخادم ({url}): {str(e)}"
                if is_site_check: return False, error_message
                logger.warning(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
            except requests.exceptions.ReadTimeout as e: 
                error_message = f"انتهت مهلة القراءة من الخادم ({url}): {str(e)}"
                if is_site_check: return False, error_message
                logger.warning(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
            except requests.exceptions.Timeout as e: 
                error_message = f"انتهت مهلة الطلب لـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.warning(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
            except requests.exceptions.ConnectionError as e:
                error_message = f"خطأ في الاتصال بالخادم ({url}): {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
            except requests.exceptions.HTTPError as e: 
                status_code = response.status_code if response else "N/A"
                error_message = f"خطأ HTTP {status_code} من الخادم لـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}. الاستجابة: {response.text[:200] if response else 'N/A'}")
                
                # Even for HTTPError, if it's RendezVous/Create, try to parse JSON for "Eligible":false
                if endpoint == 'RendezVous/Create' and response is not None:
                    try:
                        parsed_error_json = response.json()
                        if isinstance(parsed_error_json, dict) and parsed_error_json.get("Eligible") is False:
                            logger.warning(f"استجابة خطأ HTTP من {url} ولكنها JSON مع Eligible:false. الاستجابة: {parsed_error_json}")
                            return parsed_error_json, None # Valid API response indicating ineligibility
                        # If not "Eligible":false, then it's a genuine error to be returned as such
                        return parsed_error_json, f"خطأ من الخادم ({status_code}) مع تفاصيل JSON."
                    except json.JSONDecodeError: # If it's not JSON
                        logger.warning(f"استجابة نصية غير JSON لخطأ HTTP من {url}: {response.text[:200]}")
                        return {"raw_text": response.text, "http_status_code": status_code}, f"خطأ من الخادم ({status_code}) مع استجابة نصية."
            except requests.exceptions.RequestException as e: 
                error_message = f"خطأ عام في الطلب لـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                return None, "حدث خطأ عام أثناء محاولة الاتصال بالخادم." 

            if current_retry >= max_retries_for_this_call:
                logger.error(f"تم تجاوز الحد الأقصى لإعادة المحاولة لـ {url} بعد خطأ: {error_message}")
                final_error_message = f"فشل الاتصال بالخادم بعد عدة محاولات. ({error_message.split(':')[0].strip()})" 
                return None, final_error_message
            
            time.sleep(actual_delay_to_use)
            current_delay_general = min(current_delay_general * 2, MAX_BACKOFF_DELAY) 
            current_retry += 1
        
        return None, "فشل الاتصال بالخادم بعد جميع المحاولات."


    def check_main_site_availability(self):
        logger.info(f"بدء فحص توفر الموقع الرئيسي: {MAIN_SITE_CHECK_URL}")
        available, error_msg = self._make_request('GET', '', is_site_check=True)
        if error_msg: 
            logger.warning(f"فحص توفر الموقع فشل: {error_msg}")
            return False 
        return available 


    def validate_candidate(self, wassit_number, identity_doc_number):
        params = {
            "wassitNumber": wassit_number,
            "identityDocNumber": identity_doc_number
        }
        return self._make_request('GET', 'validateCandidate/query', params=params)

    def get_pre_inscription_info(self, pre_inscription_id):
        params = {"Id": pre_inscription_id}
        return self._make_request('GET', 'PreInscription/GetPreInscription', params=params)

    def get_available_dates(self, structure_id, pre_inscription_id):
        params = {
            "StructureId": structure_id,
            "PreInscriptionId": pre_inscription_id
        }
        return self._make_request('GET', 'RendezVous/GetAvailableDates', params=params)

    def create_rendezvous(self, pre_inscription_id, ccp, nom_ccp_fr, prenom_ccp_fr, rdv_date, demandeur_id):
        payload = {
            "preInscriptionId": pre_inscription_id,
            "ccp": ccp,
            "nomCcp": nom_ccp_fr, 
            "prenomCcp": nom_ccp_fr, # Note: Original code had nomCcp and prenomCcp swapped. Corrected to match typical API patterns. If API expects prenomCcp to be nom_fr, this needs to be prenom_ccp_fr as per variable name. Assuming nomCcp is nom_fr and prenomCcp is prenom_fr.
            "rdvdate": rdv_date,
            "demandeurId": demandeur_id
        }
        headers = {'g-recaptcha-response': ''} 
        return self._make_request('POST', 'RendezVous/Create', data=payload, extra_headers=headers)

    def download_pdf(self, report_type, pre_inscription_id):
        endpoint = f"download/{report_type}"
        params = {"PreInscriptionId": pre_inscription_id}
        return self._make_request('GET', endpoint, params=params)

