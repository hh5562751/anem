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

        last_error_message_for_request = "فشل غير محدد" # قيمة افتراضية للخطأ الأخير

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
                    unsupported_method_error = f"الطريقة {method} غير مدعومة لـ {url}"
                    logger.error(unsupported_method_error)
                    return None, unsupported_method_error

                logger.debug(f"استجابة الخادم لـ {url}: {response.status_code}")

                if response.status_code == 429: 
                    actual_delay_to_use = current_delay_429
                    logger.warning(f"خطأ 429 (طلبات كثيرة جدًا) من الخادم لـ {url}. الانتظار {actual_delay_to_use} ثانية.")
                    if current_retry >= max_retries_for_this_call:
                        final_429_error = "طلبات كثيرة جدًا للخادم (429). يرجى الانتظار والمحاولة لاحقًا."
                        logger.error(f"تم تجاوز الحد الأقصى لإعادة المحاولة (429) لـ {url}. الرسالة المُعادة: {final_429_error}")
                        return None, final_429_error
                    time.sleep(actual_delay_to_use)
                    current_delay_429 = min(current_delay_429 * 2, MAX_BACKOFF_DELAY) 
                    current_retry += 1
                    last_error_message_for_request = "طلبات كثيرة جدًا (429)" # تحديث رسالة الخطأ الأخيرة
                    continue
                
                actual_delay_to_use = current_delay_general # إعادة التعيين إلى التأخير العام إذا لم يكن الخطأ 429
                response.raise_for_status() 
                
                if is_site_check: 
                    return True, None 
                
                try:
                    json_response = response.json()
                    if endpoint == 'RendezVous/Create' and isinstance(json_response, dict) and json_response.get("Eligible") is False:
                        logger.warning(f"استجابة JSON من {url} تشير إلى Eligible:false. الاستجابة: {json_response}")
                        return json_response, None 
                    return json_response, None
                except json.JSONDecodeError:
                    json_decode_error_msg_short = "خطأ في تحليل البيانات المستلمة من الخادم (ليست JSON)."
                    json_decode_error_msg_full = f"خطأ في تحليل استجابة JSON من {url}. الاستجابة (أول 200 حرف): {response.text[:200] if response else 'No response object'}"
                    logger.error(json_decode_error_msg_full)
                    
                    if endpoint == 'RendezVous/Create' and response and response.text:
                        logger.warning(f"استجابة نصية غير JSON من {url} ولكنها تحتوي على نص: {response.text[:200]}")
                        if "\"Eligible\":false" in response.text.lower():
                             message_from_text = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائكم لأحد شروط الأهلية اللازمة." 
                             constructed_response = {"Eligible": False, "message": message_from_text, "raw_text": True}
                             logger.info(f"تم بناء استجابة Eligible:false من النص الخام لـ {url}: {constructed_response}")
                             return constructed_response, None

                        # إذا لم يكن Eligible:false، أرجع خطأ تحليل مع النص الخام
                        raw_text_error_detail = "استجابة نصية غير متوقعة من الخادم."
                        logger.error(f"الطلب إلى {url} فشل بسبب استجابة نصية غير متوقعة. الرسالة المُعادة: {raw_text_error_detail}")
                        return {"raw_text": response.text, "is_non_json_success_heuristic": "Eligible" in response.text}, raw_text_error_detail
                    
                    logger.error(f"الطلب إلى {url} فشل بسبب خطأ في تحليل JSON. الرسالة المُعادة: {json_decode_error_msg_short}")
                    return None, json_decode_error_msg_short

            except requests.exceptions.SSLError as e:
                error_message = f"خطأ SSL عند الاتصال بـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                last_error_message_for_request = error_message
            except requests.exceptions.ConnectTimeout as e: 
                error_message = f"انتهت مهلة الاتصال بالخادم ({url}): {str(e)}"
                if is_site_check: return False, error_message
                logger.warning(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                last_error_message_for_request = error_message
            except requests.exceptions.ReadTimeout as e: 
                error_message = f"انتهت مهلة القراءة من الخادم ({url}): {str(e)}"
                if is_site_check: return False, error_message
                logger.warning(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                last_error_message_for_request = error_message
            except requests.exceptions.Timeout as e: # هذا يشمل ConnectTimeout و ReadTimeout بشكل عام
                error_message = f"انتهت مهلة الطلب لـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.warning(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                last_error_message_for_request = error_message
            except requests.exceptions.ConnectionError as e:
                error_message = f"خطأ في الاتصال بالخادم ({url}): {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                last_error_message_for_request = error_message
            except requests.exceptions.HTTPError as e: 
                status_code = response.status_code if response else "N/A"
                error_message = f"خطأ HTTP {status_code} من الخادم لـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}. الاستجابة: {response.text[:200] if response else 'N/A'}")
                last_error_message_for_request = error_message
                
                if endpoint == 'RendezVous/Create' and response is not None:
                    try:
                        parsed_error_json = response.json()
                        if isinstance(parsed_error_json, dict) and parsed_error_json.get("Eligible") is False:
                            logger.warning(f"استجابة خطأ HTTP من {url} ولكنها JSON مع Eligible:false. الاستجابة: {parsed_error_json}")
                            return parsed_error_json, None 
                        
                        # إذا لم يكن Eligible:false، فهو خطأ حقيقي
                        http_json_error_detail = f"خطأ من الخادم ({status_code}) مع تفاصيل JSON."
                        logger.error(f"الطلب إلى {url} فشل بخطأ HTTP مع تفاصيل JSON. الرسالة المُعادة: {http_json_error_detail}")
                        return parsed_error_json, http_json_error_detail
                    except json.JSONDecodeError: 
                        http_text_error_detail = f"خطأ من الخادم ({status_code}) مع استجابة نصية."
                        logger.warning(f"استجابة نصية غير JSON لخطأ HTTP من {url}: {response.text[:200]}")
                        logger.error(f"الطلب إلى {url} فشل بخطأ HTTP مع استجابة نصية. الرسالة المُعادة: {http_text_error_detail}")
                        return {"raw_text": response.text, "http_status_code": status_code}, http_text_error_detail
            except requests.exceptions.RequestException as e: 
                error_message = f"خطأ عام في الطلب لـ {url}: {str(e)}"
                if is_site_check: return False, error_message
                logger.error(f"{log_prefix} (محاولة {current_retry + 1}): {error_message}")
                generic_request_error_msg = "حدث خطأ عام أثناء محاولة الاتصال بالخادم."
                logger.error(f"الطلب إلى {url} فشل بخطأ عام. الرسالة المُعادة: {generic_request_error_msg}")
                return None, generic_request_error_msg 

            if current_retry >= max_retries_for_this_call:
                final_error_message_after_retries = f"فشل الاتصال بالخادم بعد عدة محاولات. ({last_error_message_for_request.split(':')[0].strip()})" 
                logger.error(f"تم تجاوز الحد الأقصى لإعادة المحاولة لـ {url} بعد خطأ: {last_error_message_for_request}. الرسالة المُعادة: {final_error_message_after_retries}")
                return None, final_error_message_after_retries
            
            time.sleep(actual_delay_to_use)
            current_delay_general = min(current_delay_general * 2, MAX_BACKOFF_DELAY) 
            current_retry += 1
        
        # إذا خرج من الحلقة دون نجاح أو إرجاع مبكر
        ultimate_fallback_error = "فشل الاتصال بالخادم بعد جميع المحاولات."
        logger.error(f"الطلب إلى {url} فشل بعد جميع المحاولات (fallback). الرسالة المُعادة: {ultimate_fallback_error}")
        return None, ultimate_fallback_error


    def check_main_site_availability(self):
        logger.info(f"بدء فحص توفر الموقع الرئيسي: {MAIN_SITE_CHECK_URL}")
        # يتم التعامل مع is_site_check داخل _make_request لتعطيل إعادة المحاولة
        available, error_msg = self._make_request('GET', '', is_site_check=True) 
        if error_msg: 
            # لا نسجل كـ error هنا لأن هذا الفحص دوري، والخطأ متوقع أحيانًا
            logger.warning(f"فحص توفر الموقع فشل: {error_msg}")
            return False, error_msg # إرجاع رسالة الخطأ للمستهلك (MonitoringThread)
        return available, None


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
            "prenomCcp": prenom_ccp_fr, 
            "rdvdate": rdv_date,
            "demandeurId": demandeur_id
        }
        headers = {'g-recaptcha-response': ''} 
        return self._make_request('POST', 'RendezVous/Create', data=payload, extra_headers=headers)

    def download_pdf(self, report_type, pre_inscription_id):
        endpoint = f"download/{report_type}"
        params = {"PreInscriptionId": pre_inscription_id}
        # بالنسبة لتحميل PDF، قد تكون الاستجابة الناجحة هي البيانات الثنائية مباشرة
        # أو JSON يحتوي على base64. _make_request يتعامل مع JSON.
        # إذا كانت الاستجابة بيانات ثنائية مباشرة ولم تكن JSON، سيفشل تحليل JSON.
        # هذا يتطلب معالجة خاصة في الخيط المستدعي إذا كانت طبيعة الاستجابة يمكن أن تختلف.
        # حاليًا، الكود يفترض أن استجابة PDF الناجحة ستكون JSON مع حقل "base64Pdf".
        return self._make_request('GET', endpoint, params=params)

