# threads.py
import time
import random
import logging
import os 
import base64 
from PyQt5.QtCore import QThread, pyqtSignal, QStandardPaths 

from api_client import AnemAPIClient 
from member import Member 
from utils import get_icon_name_for_status 
from config import (
    SETTING_MIN_MEMBER_DELAY, SETTING_MAX_MEMBER_DELAY,
    SETTING_MONITORING_INTERVAL, SETTING_BACKOFF_429,
    SETTING_BACKOFF_GENERAL, SETTING_REQUEST_TIMEOUT, DEFAULT_SETTINGS
)

logger = logging.getLogger(__name__)

SHORT_SKIP_DELAY_SECONDS = 0.1 

def _translate_api_error(error_string, operation_name="العملية"):
    if not error_string:
        return f"حدث خطأ غير محدد أثناء {operation_name}."

    error_lower = str(error_string).lower()

    if "timeout" in error_lower or "timed out" in error_lower:
        if "connect" in error_lower:
            return f"انتهت مهلة الاتصال بالخادم أثناء {operation_name}. يرجى التحقق من اتصالك بالإنترنت."
        else:
            return f"انتهت مهلة الاستجابة من الخادم أثناء {operation_name}. قد يكون الخادم بطيئًا أو هناك مشكلة في الشبكة."
    elif "connectionerror" in error_lower or "could not connect" in error_lower or "failed to establish a new connection" in error_lower:
        return f"فشل الاتصال بالخادم أثناء {operation_name}. يرجى التحقق من اتصالك بالإنترنت وحالة الخادم."
    elif "sslerror" in error_lower or "certificate_verify_failed" in error_lower:
        return f"حدث خطأ في شهادة الأمان (SSL) أثناء {operation_name}. قد يكون الاتصال غير آمن."
    elif "429" in error_lower or "طلبات كثيرة جدًا" in error_lower:
        return f"الخادم مشغول حاليًا (طلبات كثيرة جدًا) أثناء {operation_name}. يرجى المحاولة لاحقًا."
    elif "404" in error_lower or "not found" in error_lower:
        return f"تعذر العثور على المورد المطلوب على الخادم (404) أثناء {operation_name}."
    elif "500" in error_lower or "internal server error" in error_lower:
        return f"حدث خطأ داخلي في الخادم (500) أثناء {operation_name}. يرجى المحاولة لاحقًا."
    elif "jsondecodeerror" in error_lower or "خطأ في تحليل البيانات" in error_lower:
        return f"تم استلام استجابة غير صالحة (ليست JSON) من الخادم أثناء {operation_name}."
    elif "eligible:false" in error_lower or "نعتذر منكم" in error_string: 
        if "نعتذر منكم! لا يمكنكم حجز موعد" in error_string:
            return error_string
        if operation_name == "حجز الموعد" and "\"Eligible\":false" in error_string and "\"serviceUp\":true" in error_string :
             return "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة."
        return f"المستخدم غير مؤهل لـ {operation_name} حسب شروط المنصة."
    
    max_len = 70
    snippet = error_string[:max_len] + "..." if len(error_string) > max_len else error_string
    return f"فشل في {operation_name}: {snippet}"


class FetchInitialInfoThread(QThread):
    update_member_gui_signal = pyqtSignal(int, str, str, str) 
    new_data_fetched_signal = pyqtSignal(int, str, str) 
    member_processing_started_signal = pyqtSignal(int) 
    member_processing_finished_signal = pyqtSignal(int) 
    global_log_signal = pyqtSignal(str, bool, object, int) 

    def __init__(self, member, index, api_client, settings, parent=None): 
        super().__init__(parent)
        self.member = member 
        self.index = index
        self.api_client = api_client
        self.settings = settings 
        self.is_running = True 

    def stop(self): 
        self.is_running = False
        logger.info(f"طلب إيقاف خيط جلب المعلومات الأولية للعضو: {self.member.nin}")

    def _emit_global_log(self, message, is_general=True):
        self.global_log_signal.emit(message, is_general, self.member if not is_general else None, self.index if not is_general else -1)

    def run(self):
        logger.info(f"بدء جلب المعلومات الأولية للعضو: {self.member.nin}")
        self.member_processing_started_signal.emit(self.index) 
        self._emit_global_log(f"جاري جلب المعلومات الأولية...", is_general=False)
        
        try:
            if not self.is_running: return 
            initial_delay = random.uniform(0.5, 1.5) 
            logger.debug(f"FetchInitialInfoThread: تأخير عشوائي {initial_delay:.2f} ثانية قبل معالجة {self.member.nin}")
            time.sleep(initial_delay)
            if not self.is_running: return 

            data_val, error_val = self.api_client.validate_candidate(self.member.wassit_no, self.member.nin)

            if not self.is_running: return 

            if error_val:
                self.member.status = "فشل التحقق الأولي"
                user_friendly_error = _translate_api_error(error_val, "التحقق من بيانات التسجيل")
                self.member.set_activity_detail(user_friendly_error, is_error=True)
                self._emit_global_log(f"فشل التحقق الأولي: {user_friendly_error}", is_general=False)
            elif data_val:
                self.member.have_allocation = data_val.get("haveAllocation", False)
                self.member.allocation_details = data_val.get("detailsAllocation", {})
                
                if self.member.have_allocation and self.member.allocation_details:
                    self.member.status = "مستفيد حاليًا من المنحة"
                    nom_ar = self.member.allocation_details.get("nomAr", "")
                    prenom_ar = self.member.allocation_details.get("prenomAr", "")
                    nom_fr = self.member.allocation_details.get("nomFr", "")
                    prenom_fr = self.member.allocation_details.get("prenomFr", "")
                    date_debut = self.member.allocation_details.get("dateDebut", "غير محدد")
                    if date_debut and "T" in date_debut: date_debut = date_debut.split("T")[0] 

                    self.member.nom_ar = nom_ar
                    self.member.prenom_ar = prenom_ar
                    self.member.nom_fr = nom_fr
                    self.member.prenom_fr = prenom_fr
                    self.new_data_fetched_signal.emit(self.index, nom_ar, prenom_ar)
                    activity_detail_text = f"مستفيد حاليًا. تاريخ بدء الاستفادة: {date_debut}."
                    self.member.set_activity_detail(activity_detail_text)
                    self._emit_global_log(f"مستفيد حاليًا.", is_general=False)
                    logger.info(f"العضو {self.member.nin} ( {self.member.get_full_name_ar()} ) مستفيد حاليًا من المنحة، تاريخ البدء: {date_debut}.")
                else:
                    is_eligible_from_validate = data_val.get("eligible", False)
                    self.member.has_actual_pre_inscription = data_val.get("havePreInscription", False)
                    self.member.already_has_rdv = data_val.get("haveRendezVous", False)
                    valid_input = data_val.get("validInput", True)
                    self.member.pre_inscription_id = data_val.get("preInscriptionId")
                    self.member.demandeur_id = data_val.get("demandeurId")
                    self.member.structure_id = data_val.get("structureId")
                    self.member.rdv_id = data_val.get("rendezVousId") 
                    
                    # Set rdv_source if RDV is discovered here
                    if self.member.already_has_rdv:
                        self.member.rdv_source = "discovered"

                    if not valid_input:
                        controls = data_val.get("controls", [])
                        error_msg_from_controls = "البيانات المدخلة غير متطابقة أو غير صالحة."
                        for control in controls: 
                            if control.get("result") is False and control.get("name") == "matchIdentity" and control.get("message"):
                                error_msg_from_controls = control.get("message")
                                break
                        self.member.status = "بيانات الإدخال خاطئة"
                        self.member.set_activity_detail(error_msg_from_controls, is_error=True)
                        self._emit_global_log(f"خطأ في بيانات الإدخال: {error_msg_from_controls}", is_general=False)
                        logger.warning(f"خطأ في بيانات الإدخال للعضو {self.member.nin}: {error_msg_from_controls}")
                    elif self.member.already_has_rdv:
                        self.member.status = "لديه موعد مسبق"
                        activity_msg = f"لديه موعد محجوز بالفعل (ID: {self.member.rdv_id or 'N/A'})."
                        if self.member.pre_inscription_id and not (self.member.nom_ar and self.member.prenom_ar):
                            if not self.is_running: return
                            data_info, error_info = self.api_client.get_pre_inscription_info(self.member.pre_inscription_id)
                            if not self.is_running: return
                            if data_info:
                                self.member.nom_ar = data_info.get("nomDemandeurAr", "")
                                self.member.prenom_ar = data_info.get("prenomDemandeurAr", "")
                                self.member.nom_fr = data_info.get("nomDemandeurFr", "")
                                self.member.prenom_fr = data_info.get("prenomDemandeurFr", "")
                                self.new_data_fetched_signal.emit(self.index, self.member.nom_ar, self.member.prenom_ar)
                                activity_msg += f" الاسم: {self.member.get_full_name_ar()}"
                                self._emit_global_log(f"تم جلب اسم العضو الذي لديه موعد.", is_general=False)
                                logger.info(f"تم جلب الاسم واللقب للعضو {self.member.nin} الذي لديه موعد مسبق.")
                            elif error_info:
                                user_friendly_error_info = _translate_api_error(error_info, "جلب معلومات التسجيل")
                                activity_msg += f" فشل جلب الاسم: {user_friendly_error_info}"
                                self._emit_global_log(f"فشل جلب اسم العضو: {user_friendly_error_info}", is_general=False)
                        self.member.set_activity_detail(activity_msg)
                    elif is_eligible_from_validate:
                        initial_status_text = ""
                        if self.member.has_actual_pre_inscription:
                            self.member.status = "تم التحقق"
                            initial_status_text = "مؤهل ولديه تسجيل مسبق."
                        else:
                            self.member.status = "يتطلب تسجيل مسبق"
                            initial_status_text = "مؤهل ولكن لا يوجد تسجيل مسبق بعد."
                        self.member.set_activity_detail(initial_status_text + " جاري جلب الاسم...")
                        
                        if self.member.pre_inscription_id and not (self.member.nom_ar and self.member.prenom_ar):
                            if not self.is_running: return
                            data_info, error_info = self.api_client.get_pre_inscription_info(self.member.pre_inscription_id)
                            if not self.is_running: return
                            if error_info:
                                self.member.status = "فشل جلب المعلومات" if self.member.status != "يتطلب تسجيل مسبق" else self.member.status
                                user_friendly_error_info = _translate_api_error(error_info, "جلب الاسم")
                                self.member.set_activity_detail(f"{initial_status_text} فشل جلب الاسم: {user_friendly_error_info}".strip(), is_error=True)
                                self._emit_global_log(f"فشل جلب اسم العضو: {user_friendly_error_info}", is_general=False)
                            elif data_info:
                                self.member.nom_ar = data_info.get("nomDemandeurAr", "")
                                self.member.prenom_ar = data_info.get("prenomDemandeurAr", "")
                                self.member.nom_fr = data_info.get("nomDemandeurFr", "")
                                self.member.prenom_fr = data_info.get("prenomDemandeurFr", "")
                                self.new_data_fetched_signal.emit(self.index, self.member.nom_ar, self.member.prenom_ar)
                                self.member.status = "تم جلب المعلومات" 
                                final_activity_text = f"تم جلب الاسم: {self.member.get_full_name_ar()}. {initial_status_text}"
                                self.member.set_activity_detail(final_activity_text)
                                self._emit_global_log(f"تم جلب اسم العضو.", is_general=False)
                                logger.info(f"تم جلب الاسم واللقب للعضو {self.member.nin}: ع ({self.member.get_full_name_ar()}), ف ({self.member.nom_fr} {self.member.prenom_fr})")
                        elif self.member.nom_ar and self.member.prenom_ar: 
                             self.member.set_activity_detail(f"{initial_status_text} الاسم: {self.member.get_full_name_ar()}")
                        else: 
                             self.member.set_activity_detail(initial_status_text)

                    else: 
                        self.member.status = "غير مؤهل مبدئيًا"
                        original_api_message = str(data_val.get("message", "المترشح غير مؤهل."))
                        self.member.set_activity_detail(original_api_message, is_error=True) 
                        self._emit_global_log(f"غير مؤهل مبدئيًا: {original_api_message}", is_general=False)
                        logger.warning(f"العضو {self.member.nin} غير مؤهل مبدئيًا: {self.member.full_last_activity_detail}")
            else: 
                self.member.status = "فشل التحقق الأولي"
                self.member.set_activity_detail("استجابة فارغة عند التحقق من بيانات التسجيل.", is_error=True)
                self._emit_global_log(f"فشل التحقق الأولي: استجابة فارغة.", is_general=False)
        except Exception as e:
            if not self.is_running: return 
            logger.exception(f"خطأ غير متوقع في FetchInitialInfoThread للعضو {self.member.nin}: {e}")
            self.member.status = "خطأ في الجلب الأولي"
            self.member.set_activity_detail(f"خطأ عام أثناء جلب المعلومات الأولية: {str(e)}", is_error=True)
            self._emit_global_log(f"خطأ في الجلب الأولي: {str(e)}", is_general=False)
        finally:
            if self.is_running: 
                final_icon = get_icon_name_for_status(self.member.status)
                self.update_member_gui_signal.emit(self.index, self.member.status, self.member.last_activity_detail, final_icon)
                self._emit_global_log(f"انتهاء جلب المعلومات الأولية. الحالة: {self.member.status}", is_general=False)
            self.member_processing_finished_signal.emit(self.index) 


class MonitoringThread(QThread):
    update_member_gui_signal = pyqtSignal(int, str, str, str) 
    new_data_fetched_signal = pyqtSignal(int, str, str)      
    global_log_signal = pyqtSignal(str, bool, object, int) 
    member_being_processed_signal = pyqtSignal(int, bool)    
    countdown_update_signal = pyqtSignal(str) 

    SITE_CHECK_INTERVAL_SECONDS = 60 
    MAX_CONSECUTIVE_MEMBER_FAILURES = 5 
    CONSECUTIVE_NETWORK_ERROR_THRESHOLD = 3 

    def __init__(self, members_list_ref, settings):
        super().__init__()
        self.members_list_ref = members_list_ref 
        self.settings = settings.copy() 
        self._apply_settings() 

        self.is_running = True 
        self.is_connection_lost_mode = False 
        self.current_member_index_to_process = 0 
        self.consecutive_network_error_trigger_count = 0 
        self.initial_scan_completed = False 

    def _apply_settings(self):
        self.interval_ms = self.settings.get(SETTING_MONITORING_INTERVAL, DEFAULT_SETTINGS[SETTING_MONITORING_INTERVAL]) * 60 * 1000
        self.min_member_delay = self.settings.get(SETTING_MIN_MEMBER_DELAY, DEFAULT_SETTINGS[SETTING_MIN_MEMBER_DELAY])
        self.max_member_delay = self.settings.get(SETTING_MAX_MEMBER_DELAY, DEFAULT_SETTINGS[SETTING_MAX_MEMBER_DELAY])
        
        self.api_client = AnemAPIClient(
            initial_backoff_general=self.settings.get(SETTING_BACKOFF_GENERAL, DEFAULT_SETTINGS[SETTING_BACKOFF_GENERAL]),
            initial_backoff_429=self.settings.get(SETTING_BACKOFF_429, DEFAULT_SETTINGS[SETTING_BACKOFF_429]),
            request_timeout=self.settings.get(SETTING_REQUEST_TIMEOUT, DEFAULT_SETTINGS[SETTING_REQUEST_TIMEOUT])
        )
        logger.info(f"MonitoringThread settings applied: Interval={self.interval_ms/60000:.1f}min, MemberDelay=[{self.min_member_delay}-{self.max_member_delay}]s")

    def _emit_global_log(self, message, is_general=True, member_obj=None, member_idx=-1):
        self.global_log_signal.emit(message, is_general, member_obj, member_idx)

    def _get_member_display_name_with_index_from_thread(self, member_obj, original_index_in_main_list):
        name_part = member_obj.get_full_name_ar()
        if not name_part or name_part.isspace():
            name_part = member_obj.nin 
        return f"{name_part} (رقم {original_index_in_main_list + 1})"

    def update_thread_settings(self, new_settings):
        logger.info("MonitoringThread: استلام طلب تحديث الإعدادات.")
        self.settings = new_settings.copy()
        self._apply_settings()

    def _wait_with_countdown(self, total_seconds, countdown_prefix=""):
        for i in range(total_seconds, 0, -1):
            if not self.is_running: break
            minutes, seconds = divmod(i, 60)
            hours, minutes = divmod(minutes, 60)
            time_str = f"{countdown_prefix}{hours:02d}:{minutes:02d}:{seconds:02d}"
            self.countdown_update_signal.emit(time_str)
            time.sleep(1)
        if self.is_running: 
            self.countdown_update_signal.emit("")


    def run(self):
        statuses_to_completely_skip_monitoring = ["مستفيد حاليًا من المنحة"]
        statuses_for_pdf_check_only = ["مكتمل", "لديه موعد مسبق"] 
        
        while self.is_running:
            if self.is_connection_lost_mode:
                self._emit_global_log(f"الاتصال بالخادم مفقود. جاري فحص توفر الموقع...")
                site_available, site_check_error = self.api_client.check_main_site_availability() 
                if not self.is_running: break

                if site_available:
                    logger.info("تم استعادة الاتصال بالخادم الرئيسي. استئناف المراقبة.")
                    self._emit_global_log("تم استعادة الاتصال بالخادم. استئناف المراقبة.")
                    self.is_connection_lost_mode = False
                    self.consecutive_network_error_trigger_count = 0 
                    logger.info("إعادة تعيين عداد الفشل المتتالي لجميع الأعضاء بعد استعادة الاتصال.")
                    for member_to_reset in self.members_list_ref:
                        member_to_reset.consecutive_failures = 0
                    continue 
                else:
                    user_friendly_site_check_error = _translate_api_error(site_check_error, "فحص توفر الموقع")
                    logger.info(f"الموقع الرئيسي لا يزال غير متاح: {user_friendly_site_check_error}. الفحص التالي بعد {self.SITE_CHECK_INTERVAL_SECONDS} ثانية.")
                    self._emit_global_log(f"الموقع لا يزال غير متاح ({user_friendly_site_check_error}).")
                    self._wait_with_countdown(self.SITE_CHECK_INTERVAL_SECONDS, "فحص الموقع بعد: ")
                    if not self.is_running: break
                    continue 
            
            if self.is_running and not self.initial_scan_completed and not self.is_connection_lost_mode:
                logger.info("بدء الفحص الأولي لجميع الأعضاء عند بدء المراقبة...")
                self._emit_global_log("جاري الفحص الأولي لجميع الأعضاء...")
                
                initial_scan_members_list = list(self.members_list_ref) 

                if not initial_scan_members_list:
                    logger.info("الفحص الأولي: لا يوجد أعضاء للفحص.")
                    self._emit_global_log("الفحص الأولي: لا يوجد أعضاء.")
                else:
                    for initial_scan_idx, member_to_process in enumerate(initial_scan_members_list):
                        if not self.is_running: break
                        
                        try:
                            actual_member_in_main_list = self.members_list_ref[initial_scan_idx]
                            if actual_member_in_main_list != member_to_process:
                                 logger.warning(f"الفحص الأولي: تم تخطي العضو (فهرس {initial_scan_idx}) لأنه تغير أو تم حذفه من القائمة الرئيسية.")
                                 continue
                        except IndexError:
                             logger.warning(f"الفحص الأولي: تم تخطي العضو (فهرس {initial_scan_idx}) لأنه لم يعد موجودًا في القائمة الرئيسية.")
                             continue

                        member_display_name = self._get_member_display_name_with_index_from_thread(member_to_process, initial_scan_idx)

                        if member_to_process.is_processing:
                            logger.debug(f"الفحص الأولي: تجاوز العضو {member_display_name} لأنه قيد المعالجة.")
                            continue

                        if member_to_process.consecutive_failures >= self.MAX_CONSECUTIVE_MEMBER_FAILURES:
                            if "فشل بشكل متكرر" not in member_to_process.status:
                                logger.warning(f"الفحص الأولي: تجاوز العضو {member_display_name} بسبب {member_to_process.consecutive_failures} محاولات فاشلة.")
                                member_to_process.status = "فشل بشكل متكرر"
                                member_to_process.set_activity_detail(f"تم تجاوز العضو بسبب {member_to_process.consecutive_failures} محاولات فاشلة متتالية.", is_error=True)
                                self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                            continue
                        
                        if member_to_process.status in statuses_to_completely_skip_monitoring:
                            logger.info(f"الفحص الأولي: تجاوز العضو {member_display_name} لأنه في حالة: {member_to_process.status}.")
                            self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                            self.member_being_processed_signal.emit(initial_scan_idx, False)
                            if self.is_running: time.sleep(SHORT_SKIP_DELAY_SECONDS)
                            continue

                        self.member_being_processed_signal.emit(initial_scan_idx, True)
                        logger.info(f"الفحص الأولي للعضو {member_display_name} - الحالة الحالية: {member_to_process.status}")
                        self._emit_global_log(f"فحص أولي...", is_general=False, member_obj=member_to_process, member_idx=initial_scan_idx)
                        
                        member_had_api_error_this_cycle = False
                        try:
                            if member_to_process.status in statuses_for_pdf_check_only:
                                logger.info(f"الفحص الأولي: العضو {member_display_name} ({member_to_process.status})، فحص PDF فقط.")
                                if member_to_process.pre_inscription_id:
                                    _, api_error_occurred_pdf = self.process_pdf_download(initial_scan_idx, member_to_process)
                                    if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                                else:
                                    member_to_process.set_activity_detail("الفحص الأولي: لا يمكن تحميل PDF، ID التسجيل مفقود.", is_error=True)
                            else: 
                                validation_success, api_error_occurred_validation = self.process_validation(initial_scan_idx, member_to_process)
                                if api_error_occurred_validation: member_had_api_error_this_cycle = True
                                if not self.is_running: break

                                is_in_stop_state_after_validation = member_to_process.status in [
                                    "مستفيد حاليًا من المنحة", "غير مؤهل مبدئيًا", "بيانات الإدخال خاطئة", 
                                    "لديه موعد مسبق", "غير مؤهل للحجز", "فشل التحقق" 
                                ]

                                if not is_in_stop_state_after_validation and validation_success:
                                    if member_to_process.pre_inscription_id and not (member_to_process.nom_ar and member_to_process.prenom_ar):
                                        if not self.is_running: break
                                        _, api_error_occurred_info = self.process_pre_inscription_info(initial_scan_idx, member_to_process)
                                        if api_error_occurred_info: member_had_api_error_this_cycle = True
                                        if not self.is_running or "فشل جلب" in member_to_process.status: pass

                                    if not self.is_running: break
                                    can_attempt_booking = member_to_process.status in ["تم جلب المعلومات", "تم التحقق", "لا توجد مواعيد", "فشل جلب التواريخ", "يتطلب تسجيل مسبق"] and \
                                                          member_to_process.has_actual_pre_inscription and member_to_process.pre_inscription_id and \
                                                          member_to_process.demandeur_id and member_to_process.structure_id and \
                                                          not member_to_process.already_has_rdv and not member_to_process.have_allocation
                                    
                                    if can_attempt_booking:
                                        _, api_error_occurred_booking = self.process_available_dates_and_book(initial_scan_idx, member_to_process)
                                        if api_error_occurred_booking: member_had_api_error_this_cycle = True
                                        if not self.is_running or member_to_process.status in ["فشل الحجز", "غير مؤهل للحجز"]: pass
                            
                            pdf_attempt_worthy_statuses_after_processing = ["تم الحجز", "مكتمل", "فشل تحميل PDF", "لديه موعد مسبق"] 
                            if member_to_process.status in pdf_attempt_worthy_statuses_after_processing and member_to_process.pre_inscription_id:
                                if not self.is_running: break
                                logger.info(f"الفحص الأولي: العضو {member_display_name} ({member_to_process.status}) يستدعي محاولة تحميل PDF.")
                                _, api_error_occurred_pdf = self.process_pdf_download(initial_scan_idx, member_to_process)
                                if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                            
                            if member_had_api_error_this_cycle:
                                member_to_process.consecutive_failures += 1
                                self.consecutive_network_error_trigger_count +=1 
                            else:
                                member_to_process.consecutive_failures = 0
                                if not member_had_api_error_this_cycle : self.consecutive_network_error_trigger_count = 0


                        except Exception as e:
                            if not self.is_running: break
                            logger.exception(f"الفحص الأولي: خطأ غير متوقع للعضو {member_display_name}: {e}")
                            member_to_process.status = "خطأ في المعالجة"
                            member_to_process.set_activity_detail(f"خطأ عام أثناء الفحص الأولي: {str(e)}", is_error=True)
                            member_to_process.consecutive_failures +=1
                            self.consecutive_network_error_trigger_count +=1
                            self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, "SP_MessageBoxCritical")
                        finally:
                            if self.is_running:
                                self.member_being_processed_signal.emit(initial_scan_idx, False)
                                self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))

                        if not self.is_running: break
                        if self.consecutive_network_error_trigger_count >= self.CONSECUTIVE_NETWORK_ERROR_THRESHOLD:
                            logger.warning(f"الفحص الأولي: {self.consecutive_network_error_trigger_count} أعضاء متتاليين واجهوا أخطاء شبكة. الدخول في وضع فحص الاتصال.")
                            self._emit_global_log("الفحص الأولي: أخطاء شبكة متتالية. إيقاف مؤقت.")
                            self.is_connection_lost_mode = True
                            break 

                        member_delay = random.uniform(self.min_member_delay, self.max_member_delay)
                        logger.info(f"الفحص الأولي: تأخير {member_delay:.2f} ثانية قبل العضو التالي.")
                        self._wait_with_countdown(int(member_delay)) 
                        if not self.is_running: break
                        if self.is_running:
                            time.sleep(member_delay - int(member_delay))
                    
                    if self.is_connection_lost_mode: 
                        continue 

                self.initial_scan_completed = True
                self.current_member_index_to_process = 0 
                logger.info("اكتمل الفحص الأولي لجميع الأعضاء.")
                self._emit_global_log("اكتمل الفحص الأولي. بدء المراقبة الدورية...")
            
            if not self.is_running: break 

            current_members_snapshot_indices = list(range(len(self.members_list_ref)))

            if not current_members_snapshot_indices: 
                logger.info("المراقبة الدورية: لا يوجد أعضاء للمراقبة.")
                self._emit_global_log("لا يوجد أعضاء للمراقبة الدورية. الانتظار...")
                self._wait_with_countdown(int(min(self.interval_ms / 1000, 30)), "الدورة التالية بعد: ")
                if not self.is_running: break
                continue 

            logger.info(f"بدء دورة مراقبة دورية... (من الفهرس {self.current_member_index_to_process}) عدد الأعضاء الكلي: {len(current_members_snapshot_indices)}")
            self._emit_global_log(f"بدء دورة مراقبة دورية... ({time.strftime('%H:%M:%S')})")

            processed_in_this_cycle = False 

            if self.current_member_index_to_process >= len(current_members_snapshot_indices):
                self.current_member_index_to_process = 0 

            start_index_for_this_run = self.current_member_index_to_process
            num_members_to_process_this_run = len(current_members_snapshot_indices)

            for i in range(num_members_to_process_this_run):
                if not self.is_running: break 

                main_list_idx = (start_index_for_this_run + i) % len(current_members_snapshot_indices) 
                
                if main_list_idx >= len(self.members_list_ref): 
                    logger.warning(f"المراقبة الدورية: تجاوز العضو (فهرس {main_list_idx}) لأنه لم يعد موجودًا.")
                    continue
                
                member_to_process = self.members_list_ref[main_list_idx]
                member_display_name_periodic = self._get_member_display_name_with_index_from_thread(member_to_process, main_list_idx)


                if member_to_process.is_processing: 
                    logger.debug(f"المراقبة الدورية: تجاوز العضو {member_display_name_periodic} لأنه قيد المعالجة.")
                    continue

                if member_to_process.consecutive_failures >= self.MAX_CONSECUTIVE_MEMBER_FAILURES:
                    if "فشل بشكل متكرر" not in member_to_process.status : 
                        logger.warning(f"المراقبة الدورية: تجاوز العضو {member_display_name_periodic} بسبب {member_to_process.consecutive_failures} محاولات فاشلة.")
                        member_to_process.status = "فشل بشكل متكرر"
                        member_to_process.set_activity_detail(f"تم تجاوز العضو بسبب {member_to_process.consecutive_failures} محاولات فاشلة متتالية.", is_error=True)
                        self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                    continue 
                
                if member_to_process.status in statuses_to_completely_skip_monitoring:
                    logger.info(f"المراقبة الدورية: تجاوز العضو {member_display_name_periodic} لأنه في حالة: {member_to_process.status}.")
                    self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                    self.member_being_processed_signal.emit(main_list_idx, False) 
                    if self.is_running: time.sleep(SHORT_SKIP_DELAY_SECONDS)
                    self.current_member_index_to_process = (main_list_idx + 1) % len(self.members_list_ref) if self.members_list_ref else 0
                    continue 

                self.member_being_processed_signal.emit(main_list_idx, True) 
                
                logger.info(f"المراقبة الدورية: فحص العضو {member_display_name_periodic} - الحالة: {member_to_process.status}")
                self._emit_global_log(f"جاري فحص دوري...", is_general=False, member_obj=member_to_process, member_idx=main_list_idx)
                
                processed_in_this_cycle = True 
                member_had_api_error_this_cycle = False 

                try:
                    if member_to_process.status in statuses_for_pdf_check_only:
                        logger.info(f"المراقبة الدورية: العضو {member_display_name_periodic} ({member_to_process.status})، فحص PDF فقط.")
                        if member_to_process.pre_inscription_id: 
                            pdf_success, api_error_occurred_pdf = self.process_pdf_download(main_list_idx, member_to_process)
                            if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                        else:
                            member_to_process.set_activity_detail("المراقبة الدورية: لا يمكن تحميل PDF، ID التسجيل مفقود.", is_error=True)
                    else: 
                        validation_success, api_error_occurred_validation = self.process_validation(main_list_idx, member_to_process)
                        if api_error_occurred_validation: member_had_api_error_this_cycle = True
                        if not self.is_running: break

                        is_in_stop_state_after_validation = member_to_process.status in [
                            "مستفيد حاليًا من المنحة", "غير مؤهل مبدئيًا", "بيانات الإدخال خاطئة", 
                            "لديه موعد مسبق", "غير مؤهل للحجز", "فشل التحقق"
                        ]

                        if not is_in_stop_state_after_validation and validation_success:
                            if member_to_process.pre_inscription_id and not (member_to_process.nom_ar and member_to_process.prenom_ar):
                                if not self.is_running: break
                                info_success, api_error_occurred_info = self.process_pre_inscription_info(main_list_idx, member_to_process)
                                if api_error_occurred_info: member_had_api_error_this_cycle = True
                                if not self.is_running or "فشل جلب" in member_to_process.status: pass 

                            if not self.is_running: break
                            can_attempt_booking = member_to_process.status in ["تم جلب المعلومات", "تم التحقق", "لا توجد مواعيد", "فشل جلب التواريخ", "يتطلب تسجيل مسبق"] and \
                                                  member_to_process.has_actual_pre_inscription and member_to_process.pre_inscription_id and \
                                                  member_to_process.demandeur_id and member_to_process.structure_id and \
                                                  not member_to_process.already_has_rdv and not member_to_process.have_allocation
                            
                            if can_attempt_booking:
                                booking_successful, api_error_occurred_booking = self.process_available_dates_and_book(main_list_idx, member_to_process)
                                if api_error_occurred_booking: member_had_api_error_this_cycle = True
                                if not self.is_running or member_to_process.status in ["فشل الحجز", "غير مؤهل للحجز"]: pass 
                    
                    pdf_attempt_worthy_statuses_after_processing = ["تم الحجز", "مكتمل", "فشل تحميل PDF", "لديه موعد مسبق"]
                    if member_to_process.status in pdf_attempt_worthy_statuses_after_processing and member_to_process.pre_inscription_id:
                        if not self.is_running: break
                        logger.info(f"المراقبة الدورية: العضو {member_display_name_periodic} ({member_to_process.status}) يستدعي محاولة تحميل PDF.")
                        pdf_success, api_error_occurred_pdf = self.process_pdf_download(main_list_idx, member_to_process)
                        if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                    
                    if member_had_api_error_this_cycle:
                        member_to_process.consecutive_failures += 1
                        self.consecutive_network_error_trigger_count +=1 
                    else: 
                        member_to_process.consecutive_failures = 0
                        self.consecutive_network_error_trigger_count = 0 

                except Exception as e:
                    if not self.is_running: break
                    logger.exception(f"المراقبة الدورية: خطأ غير متوقع للعضو {member_display_name_periodic}: {e}")
                    member_to_process.status = "خطأ في المعالجة"
                    member_to_process.set_activity_detail(f"خطأ عام أثناء المراقبة الدورية: {str(e)}", is_error=True)
                    member_to_process.consecutive_failures +=1 
                    self.consecutive_network_error_trigger_count +=1 
                    self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, "SP_MessageBoxCritical")
                finally:
                    if self.is_running:
                        self.member_being_processed_signal.emit(main_list_idx, False) 
                        self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))

                if not self.is_running: break 

                if self.consecutive_network_error_trigger_count >= self.CONSECUTIVE_NETWORK_ERROR_THRESHOLD:
                    logger.warning(f"المراقبة الدورية: {self.consecutive_network_error_trigger_count} أعضاء متتاليين واجهوا أخطاء شبكة. الدخول في وضع فحص الاتصال.")
                    self._emit_global_log("أخطاء شبكة متتالية. إيقاف مؤقت للمراقبة الدورية.")
                    self.is_connection_lost_mode = True
                    break 

                member_delay = random.uniform(self.min_member_delay, self.max_member_delay)
                logger.info(f"المراقبة الدورية: تأخير {member_delay:.2f} ثانية قبل العضو التالي.")
                self._wait_with_countdown(int(member_delay)) 
                if not self.is_running: break
                if self.is_running: 
                    time.sleep(member_delay - int(member_delay))

                self.current_member_index_to_process = (main_list_idx + 1) % len(self.members_list_ref) if self.members_list_ref else 0

            if not self.is_running: break 
            if self.is_connection_lost_mode: continue 

            self.current_member_index_to_process = 0 

            if processed_in_this_cycle:
                logger.info(f"إكمال دورة مراقبة دورية. الدورة القادمة بعد {self.interval_ms / 60000:.1f} دقيقة.")
                self._emit_global_log(f"انتهاء دورة المراقبة الدورية.")
            else: 
                logger.info(f"المراقبة الدورية: لم يتم فحص أي أعضاء. الانتظار للدورة القادمة.")
                self._emit_global_log("المراقبة الدورية: لم يتم فحص أي أعضاء مؤهلين. الانتظار...")
            
            self._wait_with_countdown(int(self.interval_ms / 1000), "الدورة التالية بعد: ")
            if not self.is_running: break
        
        logger.info("خيط المراقبة يتوقف.")
        self._emit_global_log("تم إيقاف خيط المراقبة.")


    def _update_member_and_emit(self, main_list_idx, member_obj_being_updated, new_status, detail_text, icon_name):
        member_obj_being_updated.status = new_status
        is_error_flag = "فشل" in new_status or "خطأ" in new_status or "غير مؤهل" in new_status or "بيانات الإدخال خاطئة" in new_status
        member_obj_being_updated.set_activity_detail(detail_text, is_error=is_error_flag)
        member_display_name = self._get_member_display_name_with_index_from_thread(member_obj_being_updated, main_list_idx)
        logger.info(f"تحديث حالة العضو {member_display_name}: {new_status} - التفاصيل: {member_obj_being_updated.last_activity_detail}")
        if self.is_running: 
            self.update_member_gui_signal.emit(main_list_idx, member_obj_being_updated.status, member_obj_being_updated.last_activity_detail, icon_name)

    def process_validation(self, main_list_idx, member_obj): 
        if not self.is_running: return False, False
        operation_name = "التحقق من البيانات (دوري)"
        member_display_name = self._get_member_display_name_with_index_from_thread(member_obj, main_list_idx)
        self._update_member_and_emit(main_list_idx, member_obj, "جاري التحقق (دورة)...", f"إعادة التحقق للعضو {member_display_name}", get_icon_name_for_status("جاري التحقق (دورة)..."))
        data, error = self.api_client.validate_candidate(member_obj.wassit_no, member_obj.nin)
        if not self.is_running: return False, False
        
        new_status = member_obj.status 
        validation_can_progress = False 
        api_error_occurred = False 
        detail_text_for_gui = member_obj.last_activity_detail 

        if error:
            new_status = "فشل التحقق"
            detail_text_for_gui = _translate_api_error(error, operation_name)
            api_error_occurred = True
            self._emit_global_log(f"فشل التحقق الدوري: {detail_text_for_gui}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        elif data:
            member_obj.have_allocation = data.get("haveAllocation", False)
            member_obj.allocation_details = data.get("detailsAllocation", {})

            if member_obj.have_allocation and member_obj.allocation_details:
                new_status = "مستفيد حاليًا من المنحة"
                nom_ar = member_obj.allocation_details.get("nomAr", member_obj.nom_ar) 
                prenom_ar = member_obj.allocation_details.get("prenomAr", member_obj.prenom_ar)
                nom_fr = member_obj.allocation_details.get("nomFr", member_obj.nom_fr)
                prenom_fr = member_obj.allocation_details.get("prenomFr", member_obj.prenom_fr)
                date_debut = member_obj.allocation_details.get("dateDebut", "غير محدد")
                if date_debut and "T" in date_debut: date_debut = date_debut.split("T")[0]

                if nom_ar != member_obj.nom_ar or prenom_ar != member_obj.prenom_ar: 
                    member_obj.nom_ar = nom_ar
                    member_obj.prenom_ar = prenom_ar
                    member_obj.nom_fr = nom_fr
                    member_obj.prenom_fr = prenom_fr
                    if self.is_running: self.new_data_fetched_signal.emit(main_list_idx, nom_ar, prenom_ar) 
                
                detail_text_for_gui = f"مستفيد حاليًا. تاريخ بدء الاستفادة: {date_debut}."
                self._emit_global_log(f"مستفيد حاليًا.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                validation_can_progress = False 
            else: 
                member_obj.has_actual_pre_inscription = data.get("havePreInscription", False)
                member_obj.already_has_rdv = data.get("haveRendezVous", False)
                valid_input = data.get("validInput", True)
                member_obj.pre_inscription_id = data.get("preInscriptionId")
                member_obj.demandeur_id = data.get("demandeurId")
                member_obj.structure_id = data.get("structureId")
                member_obj.rdv_id = data.get("rendezVousId") 

                if member_obj.already_has_rdv and member_obj.rdv_source != "system": # Don't overwrite if system booked it
                    member_obj.rdv_source = "discovered"

                if not valid_input:
                    controls = data.get("controls", [])
                    error_msg_from_controls = "البيانات المدخلة غير متطابقة أو غير صالحة."
                    for control in controls:
                        if control.get("result") is False and control.get("name") == "matchIdentity" and control.get("message"):
                            error_msg_from_controls = control.get("message")
                            break
                    new_status = "بيانات الإدخال خاطئة"
                    detail_text_for_gui = error_msg_from_controls
                    self._emit_global_log(f"خطأ في بيانات الإدخال (دوري): {error_msg_from_controls}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                elif member_obj.already_has_rdv:
                    new_status = "لديه موعد مسبق"
                    detail_text_for_gui = f"لديه موعد محجوز بالفعل (ID: {member_obj.rdv_id or 'N/A'})."
                    self._emit_global_log(f"لديه موعد مسبق.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                    if member_obj.pre_inscription_id and not (member_obj.nom_ar and member_obj.prenom_ar):
                        validation_can_progress = True 
                    else:
                        validation_can_progress = False 
                elif data.get("eligible", False) and member_obj.has_actual_pre_inscription:
                    new_status = "تم التحقق" 
                    detail_text_for_gui = "مؤهل ولديه تسجيل مسبق (دورة)."
                    validation_can_progress = True
                elif data.get("eligible", False) and not member_obj.has_actual_pre_inscription:
                    new_status = "يتطلب تسجيل مسبق" 
                    detail_text_for_gui = "مؤهل ولكن لا يوجد تسجيل مسبق بعد (بانتظار توفر موعد)."
                    validation_can_progress = True 
                elif not data.get("eligible", False): 
                    new_status = "غير مؤهل للحجز" 
                    detail_text_for_gui = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة."
                    if isinstance(data, dict) and "message" in data and data["message"]:
                         detail_text_for_gui = data["message"] 
                    elif isinstance(data, dict) and data.get("Eligible") is False and data.get("serviceUp") is True: 
                         detail_text_for_gui = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة."

                    self._emit_global_log(f"غير مؤهل للحجز (دوري): {detail_text_for_gui}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                else: 
                    new_status = "فشل التحقق" 
                    detail_text_for_gui = "حالة غير معروفة بعد التحقق من البيانات (دوري)."
                    api_error_occurred = True
                    self._emit_global_log(f"فشل التحقق الدوري: حالة غير معروفة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        else: 
            new_status = "فشل التحقق"
            detail_text_for_gui = "استجابة فارغة من الخادم عند التحقق من البيانات (دوري)."
            api_error_occurred = True
            self._emit_global_log(f"فشل التحقق الدوري: استجابة فارغة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        
        icon = get_icon_name_for_status(new_status) 
        self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, icon)
        return validation_can_progress, api_error_occurred

    def process_pre_inscription_info(self, main_list_idx, member_obj): 
        if not self.is_running: return False, False
        operation_name = "جلب معلومات الاسم"
        member_display_name = self._get_member_display_name_with_index_from_thread(member_obj, main_list_idx)
        if not member_obj.pre_inscription_id:
            detail_text = "ID التسجيل المسبق غير متوفر لجلب الاسم."
            self._update_member_and_emit(main_list_idx, member_obj, member_obj.status, detail_text, get_icon_name_for_status(member_obj.status))
            return False, False 
        
        self._update_member_and_emit(main_list_idx, member_obj, "جاري جلب الاسم...", f"محاولة جلب الاسم واللقب للعضو {member_display_name}", get_icon_name_for_status("جاري جلب الاسم..."))
        data, error = self.api_client.get_pre_inscription_info(member_obj.pre_inscription_id)
        if not self.is_running: return False, False
        
        new_status = member_obj.status 
        icon = get_icon_name_for_status(new_status)
        info_fetched_successfully = False
        api_error_occurred = False
        detail_text_for_gui = member_obj.last_activity_detail

        if error:
            if "جاري جلب الاسم..." in new_status : new_status = "فشل جلب المعلومات" 
            detail_text_for_gui = _translate_api_error(error, operation_name)
            api_error_occurred = True
            self._emit_global_log(f"فشل جلب اسم العضو: {detail_text_for_gui}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        elif data:
            member_obj.nom_fr = data.get("nomDemandeurFr", "")
            member_obj.prenom_fr = data.get("prenomDemandeurFr", "")
            member_obj.nom_ar = data.get("nomDemandeurAr", "")
            member_obj.prenom_ar = data.get("prenomDemandeurAr", "")
            
            current_activity = member_obj.last_activity_detail.replace(" جاري جلب الاسم...", "").strip() 
            
            if "جاري جلب الاسم..." in new_status or new_status == "تم التحقق": 
                if member_obj.already_has_rdv: 
                    new_status = "لديه موعد مسبق" 
                    detail_text_for_gui = f"لديه موعد محجوز بالفعل. الاسم: {member_obj.get_full_name_ar()}"
                else: 
                    new_status = "تم جلب المعلومات" 
                    detail_text_for_gui = f"تم جلب الاسم: {member_obj.get_full_name_ar()}. {current_activity}"
            elif member_obj.status == "لديه موعد مسبق": 
                 detail_text_for_gui = f"لديه موعد محجوز بالفعل. الاسم: {member_obj.get_full_name_ar()}"
            else: 
                 new_status = "تم جلب المعلومات"
                 detail_text_for_gui = f"تم جلب الاسم: {member_obj.get_full_name_ar()}. {current_activity}"
            
            detail_text_for_gui = detail_text_for_gui.strip()
            if self.is_running: self.new_data_fetched_signal.emit(main_list_idx, member_obj.nom_ar, member_obj.prenom_ar) 
            self._emit_global_log(f"تم جلب اسم العضو.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
            info_fetched_successfully = True
        else: 
            if "جاري جلب الاسم..." in new_status : new_status = "فشل جلب المعلومات"
            detail_text_for_gui = "استجابة فارغة عند جلب معلومات الاسم."
            api_error_occurred = True 
            self._emit_global_log(f"فشل جلب اسم العضو: استجابة فارغة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        
        icon = get_icon_name_for_status(new_status)
        self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, icon)
        return info_fetched_successfully, api_error_occurred


    def process_available_dates_and_book(self, main_list_idx, member_obj): 
        if not self.is_running: return False, False
        operation_name_dates = "البحث عن مواعيد متاحة"
        operation_name_book = "حجز الموعد"
        member_display_name = self._get_member_display_name_with_index_from_thread(member_obj, main_list_idx)

        if not (member_obj.structure_id and member_obj.pre_inscription_id and member_obj.demandeur_id and member_obj.has_actual_pre_inscription):
            detail_text = "معلومات ناقصة أو التسجيل المسبق غير مؤكد لمحاولة الحجز."
            self._update_member_and_emit(main_list_idx, member_obj, member_obj.status, detail_text, get_icon_name_for_status(member_obj.status))
            return False, False 
        
        self._update_member_and_emit(main_list_idx, member_obj, "جاري البحث عن مواعيد...", f"البحث عن مواعيد للعضو {member_display_name}", get_icon_name_for_status("جاري البحث عن مواعيد..."))
        self._emit_global_log(f"جاري البحث عن مواعيد...", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        data, error = self.api_client.get_available_dates(member_obj.structure_id, member_obj.pre_inscription_id)
        if not self.is_running: return False, False
        
        new_status = member_obj.status
        icon = get_icon_name_for_status(new_status)
        booking_successful = False
        api_error_occurred_this_stage = False 
        detail_text_for_gui = member_obj.last_activity_detail

        if error:
            new_status = "فشل جلب التواريخ"
            detail_text_for_gui = _translate_api_error(error, operation_name_dates)
            api_error_occurred_this_stage = True
            self._emit_global_log(f"فشل جلب التواريخ: {detail_text_for_gui}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        elif data and "dates" in data:
            available_dates = data["dates"]
            if available_dates:
                selected_date_str = available_dates[0] 
                try:
                    day, month, year = selected_date_str.split('/')
                    formatted_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}" 
                except ValueError:
                    new_status = "خطأ في تنسيق التاريخ"
                    detail_text_for_gui = f"تنسيق تاريخ غير صالح من الخادم: {selected_date_str}"
                    api_error_occurred_this_stage = True 
                    self._emit_global_log(f"خطأ في تنسيق التاريخ من الخادم: {selected_date_str}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                    self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, get_icon_name_for_status(new_status))
                    return False, api_error_occurred_this_stage
                
                self._update_member_and_emit(main_list_idx, member_obj, "جاري حجز الموعد...", f"محاولة الحجز في {formatted_date}", get_icon_name_for_status("جاري حجز الموعد..."))
                self._emit_global_log(f"جاري حجز موعد في تاريخ {formatted_date}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                if not (member_obj.ccp and member_obj.nom_fr and member_obj.prenom_fr):
                    new_status = "فشل الحجز"
                    detail_text_for_gui = "معلومات CCP أو الاسم الفرنسي مفقودة للحجز."
                    self._emit_global_log(f"فشل حجز الموعد: معلومات ناقصة (CCP أو الاسم الفرنسي).", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                    self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, get_icon_name_for_status(new_status))
                    return False, False 
                
                if not self.is_running: return False, api_error_occurred_this_stage 
                book_data, book_error = self.api_client.create_rendezvous(
                    member_obj.pre_inscription_id, member_obj.ccp, member_obj.nom_fr, member_obj.prenom_fr,
                    formatted_date, member_obj.demandeur_id
                )
                if not self.is_running: return False, api_error_occurred_this_stage 

                if book_error: 
                    new_status = "فشل الحجز"
                    detail_text_for_gui = _translate_api_error(book_error, operation_name_book)
                    api_error_occurred_this_stage = True
                    self._emit_global_log(f"فشل حجز الموعد: {detail_text_for_gui}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                elif book_data: 
                    if isinstance(book_data, dict) and book_data.get("Eligible") is False and book_data.get("serviceUp") is True:
                        new_status = "غير مؤهل للحجز"
                        api_message = book_data.get("message") 
                        if not api_message or not isinstance(api_message, str) or api_message.strip() == "":
                             api_message = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة."
                        detail_text_for_gui = api_message
                        self._emit_global_log(f"غير مؤهل للحجز: {api_message}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                        logger.warning(f"العضو {member_display_name} غير مؤهل للحجز (Eligible:false, serviceUp:true): {book_data}")
                        api_error_occurred_this_stage = False 
                    elif isinstance(book_data, dict) and book_data.get("Eligible") is False : 
                        new_status = "غير مؤهل للحجز"
                        api_message = book_data.get("message", "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة.")
                        detail_text_for_gui = api_message
                        self._emit_global_log(f"غير مؤهل للحجز: {api_message}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                        logger.warning(f"العضو {member_display_name} غير مؤهل للحجز حسب استجابة الخادم: {book_data}")
                        api_error_occurred_this_stage = False 
                    elif isinstance(book_data, dict) and book_data.get("code") == 0 and book_data.get("rendezVousId"): 
                        member_obj.rdv_id = book_data.get("rendezVousId")
                        member_obj.rdv_date = formatted_date 
                        member_obj.rdv_source = "system" # Set source to system
                        new_status = "تم الحجز"
                        detail_text_for_gui = f"تم الحجز بنجاح في: {formatted_date}, ID: {member_obj.rdv_id}"
                        self._emit_global_log(f"تم حجز موعد بنجاح في {formatted_date}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                        booking_successful = True
                    else: 
                        new_status = "فشل الحجز"
                        err_msg_detail = str(book_data.get("message", "خطأ غير معروف من الخادم عند الحجز")) if isinstance(book_data, dict) else str(book_data)
                        
                        if isinstance(book_data, dict) and "raw_text" in book_data and "\"Eligible\":false" in book_data["raw_text"].lower(): 
                             new_status = "غير مؤهل للحجز"
                             raw_text_message = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة. (استجابة نصية)"
                             try:
                                 parsed_raw = json.loads(book_data["raw_text"])
                                 if "message" in parsed_raw: raw_text_message = parsed_raw["message"]
                             except: pass 

                             detail_text_for_gui = raw_text_message
                             self._emit_global_log(f"غير مؤهل للحجز (استجابة نصية): {raw_text_message}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                             logger.warning(f"العضو {member_display_name} غير مؤهل للحجز (استجابة نصية): {book_data['raw_text'][:200]}")
                             api_error_occurred_this_stage = False
                        else:
                            detail_text_for_gui = f"فشل الحجز: {err_msg_detail}"
                            api_error_occurred_this_stage = True 
                            self._emit_global_log(f"فشل حجز الموعد: {detail_text_for_gui}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                else: 
                    new_status = "فشل الحجز"
                    detail_text_for_gui = "استجابة غير متوقعة أو فارغة عند محاولة الحجز."
                    api_error_occurred_this_stage = True
                    self._emit_global_log(f"فشل حجز الموعد: استجابة غير متوقعة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
            else: 
                new_status = "لا توجد مواعيد"
                detail_text_for_gui = "لا توجد مواعيد متاحة حاليًا للحجز."
                self._emit_global_log(f"لا توجد مواعيد متاحة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
                if not member_obj.has_actual_pre_inscription: 
                    new_status = "يتطلب تسجيل مسبق"
                    detail_text_for_gui = "مؤهل ولكن لا يوجد تسجيل مسبق بعد (لا مواعيد متاحة حاليًا)."
        else: 
            new_status = "فشل جلب التواريخ"
            detail_text_for_gui = "لم يتم العثور على تواريخ أو استجابة غير صالحة من الخادم."
            api_error_occurred_this_stage = True
            self._emit_global_log(f"فشل جلب التواريخ: استجابة غير صالحة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        
        icon = get_icon_name_for_status(new_status)
        self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, icon)
        return booking_successful, api_error_occurred_this_stage

    def _download_single_pdf_for_monitoring(self, main_list_idx, member_obj, report_type, filename_suffix_base, member_specific_dir):
        if not self.is_running: return None, False, "", ""
        operation_name = f"تحميل شهادة {filename_suffix_base}"
        member_display_name = self._get_member_display_name_with_index_from_thread(member_obj, main_list_idx)
        file_path = None
        success = False
        error_msg_for_toast = ""
        status_msg_for_gui_cell = f"جاري تحميل {filename_suffix_base}..."
        
        current_path_attr = 'pdf_honneur_path' if report_type == "HonneurEngagementReport" else 'pdf_rdv_path'
        
        current_pdf_path_value = getattr(member_obj, current_path_attr)
        if current_pdf_path_value and os.path.exists(current_pdf_path_value):
            logger.info(f"ملف {report_type} موجود بالفعل للعضو {member_display_name} في {current_pdf_path_value}. تخطي التحميل.")
            return current_pdf_path_value, True, "", f"شهادة {filename_suffix_base} موجودة بالفعل."

        self._update_member_and_emit(main_list_idx, member_obj, status_msg_for_gui_cell, f"بدء تحميل {report_type}", get_icon_name_for_status(status_msg_for_gui_cell))
        self._emit_global_log(f"جاري تحميل شهادة {filename_suffix_base}...", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        if not self.is_running: return None, False, "", "" 
        response_data, api_err = self.api_client.download_pdf(report_type, member_obj.pre_inscription_id)
        if not self.is_running: return None, False, "", "" 

        if api_err:
            error_msg_for_toast = _translate_api_error(api_err, operation_name)
            self._emit_global_log(f"فشل تحميل شهادة {filename_suffix_base}: {error_msg_for_toast}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        elif response_data and (isinstance(response_data, str) or (isinstance(response_data, dict) and "base64Pdf" in response_data)):
            pdf_b64 = response_data if isinstance(response_data, str) else response_data.get("base64Pdf")
            try:
                pdf_content = base64.b64decode(pdf_b64)
                safe_member_name_part = "".join(c for c in (member_obj.get_full_name_ar() or member_obj.nin) if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ","_")
                if not safe_member_name_part: safe_member_name_part = member_obj.nin 
                final_filename = f"{filename_suffix_base}_{safe_member_name_part}.pdf" 
                file_path = os.path.join(member_specific_dir, final_filename)
                with open(file_path, 'wb') as f:
                    f.write(pdf_content)
                setattr(member_obj, current_path_attr, file_path) 
                success = True
                status_msg_for_gui_cell = f"تم تحميل {final_filename} بنجاح."
                self._emit_global_log(f"تم تحميل شهادة {filename_suffix_base} بنجاح.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
            except Exception as e_save:
                error_msg_for_toast = f"خطأ في حفظ ملف {report_type}: {str(e_save)}"
                self._emit_global_log(f"خطأ في حفظ شهادة {filename_suffix_base}: {e_save}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        else:
            error_msg_for_toast = f"استجابة غير متوقعة من الخادم لـ {operation_name}."
            self._emit_global_log(f"فشل تحميل شهادة {filename_suffix_base}: استجابة غير متوقعة.", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
        
        if not success:
            status_msg_for_gui_cell = f"فشل تحميل {filename_suffix_base}: {error_msg_for_toast.split(':')[0]}" 
        
        return file_path, success, error_msg_for_toast, status_msg_for_gui_cell

    def process_pdf_download(self, main_list_idx, member_obj): 
        if not self.is_running: return False, False
        member_display_name = self._get_member_display_name_with_index_from_thread(member_obj, main_list_idx)
        if not member_obj.pre_inscription_id:
            detail_text = "ID التسجيل مفقود لتحميل PDF."
            self._update_member_and_emit(main_list_idx, member_obj, member_obj.status, detail_text, get_icon_name_for_status(member_obj.status))
            return False, False 
        
        documents_location = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base_app_dir_name = "ملفات_المنحة_البرنامج"
        member_name_for_folder = member_obj.get_full_name_ar()
        if not member_name_for_folder or member_name_for_folder.isspace(): 
            member_name_for_folder = member_obj.nin 
        
        safe_folder_name_part = "".join(c for c in member_name_for_folder if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ", "_")
        if not safe_folder_name_part: safe_folder_name_part = member_obj.nin 
        
        member_specific_output_dir = os.path.join(documents_location, base_app_dir_name, safe_folder_name_part)
        
        try:
            os.makedirs(member_specific_output_dir, exist_ok=True) 
        except Exception as e_mkdir:
            logger.error(f"فشل إنشاء مجلد للعضو {member_display_name} في process_pdf_download: {e_mkdir}")
            user_friendly_mkdir_error = f"فشل إنشاء مجلد لحفظ الملفات: {e_mkdir}"
            self._update_member_and_emit(main_list_idx, member_obj, "فشل تحميل PDF", user_friendly_mkdir_error, get_icon_name_for_status("فشل تحميل PDF"))
            self._emit_global_log(f"فشل إنشاء مجلد: {e_mkdir}", is_general=False, member_obj=member_obj, member_idx=main_list_idx)
            return False, False 
        
        all_relevant_pdfs_downloaded_successfully = True
        any_api_error_this_pdf_stage = False
        download_details_agg = [] 

        if not self.is_running: return False, any_api_error_this_pdf_stage 
        fp_h, s_h, err_h, stat_h = self._download_single_pdf_for_monitoring(main_list_idx, member_obj, "HonneurEngagementReport", "التزام", member_specific_output_dir)
        download_details_agg.append(stat_h)
        if not s_h: all_relevant_pdfs_downloaded_successfully = False
        if err_h: any_api_error_this_pdf_stage = True 
        
        if self.is_running and (member_obj.already_has_rdv or member_obj.rdv_id): 
            fp_r, s_r, err_r, stat_r = self._download_single_pdf_for_monitoring(main_list_idx, member_obj, "RdvReport", "موعد", member_specific_output_dir)
            download_details_agg.append(stat_r)
            if not s_r: all_relevant_pdfs_downloaded_successfully = False
            if err_r: any_api_error_this_pdf_stage = True
        elif self.is_running: 
            msg_skip_rdv = "شهادة الموعد غير مطلوبة (لا يوجد موعد مسجل)."
            logger.info(msg_skip_rdv + f" للعضو {member_display_name}")
            download_details_agg.append(msg_skip_rdv)
        
        final_status_after_pdfs = member_obj.status
        if all_relevant_pdfs_downloaded_successfully:
            if member_obj.status != "مستفيد حاليًا من المنحة": 
                 final_status_after_pdfs = "مكتمل"
        else:
            if "فشل تحميل PDF" not in final_status_after_pdfs and member_obj.status != "مستفيد حاليًا من المنحة": 
                final_status_after_pdfs = "فشل تحميل PDF" 
            
        final_detail_message = "; ".join(msg for msg in download_details_agg if msg) 
        self._update_member_and_emit(main_list_idx, member_obj, final_status_after_pdfs, final_detail_message, get_icon_name_for_status(final_status_after_pdfs))
        
        return all_relevant_pdfs_downloaded_successfully, any_api_error_this_pdf_stage

    def stop_monitoring(self): 
        logger.info("طلب إيقاف المراقبة...")
        self.is_running = False


class SingleMemberCheckThread(QThread):
    update_member_gui_signal = pyqtSignal(int, str, str, str) 
    new_data_fetched_signal = pyqtSignal(int, str, str)      
    member_processing_started_signal = pyqtSignal(int)       
    member_processing_finished_signal = pyqtSignal(int)      
    global_log_signal = pyqtSignal(str, bool, object, int) 

    def __init__(self, member, index, api_client, settings, parent=None):
        super().__init__(parent)
        self.member = member 
        self.index = index   
        self.api_client = api_client
        self.settings = settings 
        self.is_running = True 

    def stop(self):
        self.is_running = False
        logger.info(f"طلب إيقاف خيط الفحص الفردي للعضو: {self.member.nin}")

    def _emit_global_log(self, message, is_general=True): 
        self.global_log_signal.emit(message, is_general, self.member if not is_general else None, self.index if not is_general else -1)


    def run(self):
        member_display_name = f"{self.member.get_full_name_ar() or self.member.nin} (رقم {self.index + 1})"
        logger.info(f"بدء فحص فوري للعضو: {member_display_name}")
        self.member_processing_started_signal.emit(self.index) 
        self._emit_global_log(f"بدء الفحص الفوري...")

        member_had_api_error_overall = False 
        
        temp_monitor_logic_provider = MonitoringThread(members_list_ref=[self.member], settings=self.settings) 
        temp_monitor_logic_provider.is_running = self.is_running 
        temp_monitor_logic_provider.update_member_gui_signal.connect(self._handle_temp_monitor_gui_update) 
        temp_monitor_logic_provider.new_data_fetched_signal.connect(self.new_data_fetched_signal)
        temp_monitor_logic_provider.global_log_signal.connect(self.global_log_signal)


        try:
            if not self.is_running: return 

            self.member.status = "جاري التحقق (فوري)..." 
            self.member.set_activity_detail(f"التحقق من صحة بيانات {member_display_name}")
            self._emit_gui_update() 
            if not self.is_running: return

            validation_can_progress, api_error_validation = temp_monitor_logic_provider.process_validation(0, self.member) 
            if api_error_validation: member_had_api_error_overall = True
            if not self.is_running: return

            if self.member.status in ["مستفيد حاليًا من المنحة", "بيانات الإدخال خاطئة", "فشل التحقق", "غير مؤهل مبدئيًا", "لديه موعد مسبق", "غير مؤهل للحجز"]:
                logger.info(f"الفحص الفوري: الحالة النهائية بعد التحقق أو حالة تمنع المتابعة: {self.member.status}")
                return 

            if validation_can_progress and self.member.pre_inscription_id and not (self.member.nom_ar and self.member.prenom_ar):
                if not self.is_running: return
                info_success, api_error_info = temp_monitor_logic_provider.process_pre_inscription_info(0, self.member)
                if api_error_info: member_had_api_error_overall = True
                if not self.is_running: return
                if self.member.status == "فشل جلب المعلومات": 
                    logger.info(f"الفحص الفوري: فشل جلب الاسم.")
                    return
            
            can_attempt_booking_single = self.member.status in ["تم جلب المعلومات", "تم التحقق", "لا توجد مواعيد", "فشل جلب التواريخ", "يتطلب تسجيل مسبق"] and \
                                         self.member.has_actual_pre_inscription and self.member.pre_inscription_id and \
                                         self.member.demandeur_id and self.member.structure_id and \
                                         not self.member.already_has_rdv and not self.member.have_allocation
            if can_attempt_booking_single: 
                if not self.is_running: return
                booking_successful, api_error_booking = temp_monitor_logic_provider.process_available_dates_and_book(0, self.member)
                if api_error_booking: member_had_api_error_overall = True
                if not self.is_running: return
                if self.member.status in ["فشل الحجز", "غير مؤهل للحجز"]:
                    logger.info(f"الفحص الفوري: فشل الحجز أو غير مؤهل.")
                    return
            
            pdf_attempt_worthy_statuses_for_single_check = ["تم الحجز", "لديه موعد مسبق", "مستفيد حاليًا من المنحة", "مكتمل", "فشل تحميل PDF"] 
            if self.member.status in pdf_attempt_worthy_statuses_for_single_check and self.member.pre_inscription_id:
                if not self.is_running: return
                logger.info(f"الفحص الفوري للعضو {member_display_name} ({self.member.status}) يستدعي محاولة تحميل PDF.")
                pdf_success, api_error_pdf = temp_monitor_logic_provider.process_pdf_download(0, self.member)
                if api_error_pdf: member_had_api_error_overall = True
                if not self.is_running: return
            
            final_log_message = f"الفحص الفوري للعضو {member_display_name} انتهى بالحالة: {self.member.status}. التفاصيل: {self.member.full_last_activity_detail}"
            logger.info(final_log_message)
            self._emit_global_log(f"فحص انتهى بالحالة: {self.member.status} - {self.member.last_activity_detail}")

        except Exception as e:
            if not self.is_running: return
            logger.exception(f"خطأ غير متوقع في SingleMemberCheckThread للعضو {member_display_name}: {e}")
            self.member.status = "خطأ في الفحص الفوري"
            self.member.set_activity_detail(f"خطأ عام أثناء الفحص الفوري: {str(e)}", is_error=True)
            self._emit_global_log(f"خطأ فحص: {str(e)}")
        finally:
            temp_monitor_logic_provider.is_running = False 
            if self.is_running: 
                self._emit_gui_update() 
            self.member_processing_finished_signal.emit(self.index) 
            logger.info(f"انتهاء الفحص الفوري للعضو: {member_display_name}")

    def _handle_temp_monitor_gui_update(self, original_idx_ignored, status_text, detail_text, icon_name_str):
        if self.is_running:
            self.member.status = status_text 
            is_error = "فشل" in status_text or "خطأ" in status_text or "غير مؤهل" in status_text
            self.member.set_activity_detail(detail_text, is_error=is_error)
            self.update_member_gui_signal.emit(self.index, self.member.status, self.member.last_activity_detail, icon_name_str)


    def _emit_gui_update(self):
        if not self.is_running: return 
        final_icon = get_icon_name_for_status(self.member.status)
        self.update_member_gui_signal.emit(self.index, self.member.status, self.member.last_activity_detail, final_icon)


class DownloadAllPdfsThread(QThread): 
    all_pdfs_download_finished_signal = pyqtSignal(int, str, str, str, bool, str) 
    individual_pdf_status_signal = pyqtSignal(int, str, str, bool, str) 
    member_processing_started_signal = pyqtSignal(int)
    member_processing_finished_signal = pyqtSignal(int)
    global_log_signal = pyqtSignal(str, bool, object, int) 

    def __init__(self, member, index, api_client, parent=None):
        super().__init__(parent)
        self.member = member
        self.index = index
        self.api_client = api_client
        self.is_running = True 

    def _emit_global_log(self, message, is_general=True): 
        self.global_log_signal.emit(message, is_general, self.member if not is_general else None, self.index if not is_general else -1)

    def _get_member_display_name_with_index_from_thread(self, member_obj, original_index_in_main_list):
        name_part = member_obj.get_full_name_ar()
        if not name_part or name_part.isspace():
            name_part = member_obj.nin 
        return f"{name_part} (رقم {original_index_in_main_list + 1})"


    def _download_single_pdf(self, pdf_type, filename_suffix_base, member_specific_dir):
        if not self.is_running: return None, False, "", ""
        operation_name = f"تحميل شهادة {filename_suffix_base}"
        member_display_name = self._get_member_display_name_with_index_from_thread(self.member, self.index)
        file_path = None
        success = False
        error_msg_toast = "" 
        status_for_gui_cell = f"جاري تحميل {filename_suffix_base}..."
        self._emit_global_log(f"{status_for_gui_cell}...")

        if not self.member.pre_inscription_id:
            error_msg_toast = "ID التسجيل المسبق مفقود."
            status_for_gui_cell = f"فشل: {error_msg_toast}"
            if self.is_running: self.individual_pdf_status_signal.emit(self.index, pdf_type, status_for_gui_cell, False, error_msg_toast)
            return None, False, error_msg_toast, status_for_gui_cell

        current_path_attr = 'pdf_honneur_path' if pdf_type == "HonneurEngagementReport" else 'pdf_rdv_path'
        
        current_pdf_path_value = getattr(self.member, current_path_attr)
        if current_pdf_path_value and os.path.exists(current_pdf_path_value):
            logger.info(f"ملف {pdf_type} موجود بالفعل للعضو {member_display_name} في {current_pdf_path_value}. تخطي التحميل.")
            status_for_gui_cell = f"شهادة {filename_suffix_base} موجودة بالفعل."
            if self.is_running: self.individual_pdf_status_signal.emit(self.index, pdf_type, current_pdf_path_value, True, "") 
            return current_pdf_path_value, True, "", status_for_gui_cell

        if not self.is_running: return None, False, "", ""
        response_data, api_err = self.api_client.download_pdf(pdf_type, self.member.pre_inscription_id)
        if not self.is_running: return None, False, "", ""


        if api_err:
            error_msg_toast = _translate_api_error(api_err, operation_name)
        elif response_data and (isinstance(response_data, str) or (isinstance(response_data, dict) and "base64Pdf" in response_data)):
            pdf_b64 = response_data if isinstance(response_data, str) else response_data.get("base64Pdf")
            try:
                pdf_content = base64.b64decode(pdf_b64)
                safe_member_name_part = "".join(c for c in (self.member.get_full_name_ar() or self.member.nin) if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ","_")
                if not safe_member_name_part: safe_member_name_part = self.member.nin 
                filename = f"{filename_suffix_base}_{safe_member_name_part}.pdf" 
                file_path = os.path.join(member_specific_dir, filename)
                with open(file_path, 'wb') as f:
                    f.write(pdf_content)
                setattr(self.member, current_path_attr, file_path) 
                success = True
                status_for_gui_cell = f"تم تحميل {filename} بنجاح."
            except Exception as e_save:
                error_msg_toast = f"خطأ في حفظ ملف {report_type}: {str(e_save)}"
        else:
            error_msg_toast = f"استجابة غير متوقعة من الخادم لـ {operation_name}."
        
        if not success:
            status_for_gui_cell = f"فشل تحميل {filename_suffix_base}: {error_msg_toast.split(':')[0]}"
        
        if self.is_running: self.individual_pdf_status_signal.emit(self.index, pdf_type, file_path if success else status_for_gui_cell, success, error_msg_toast)
        return file_path, success, error_msg_toast, status_for_gui_cell

    def run(self):
        member_display_name = self._get_member_display_name_with_index_from_thread(self.member, self.index)
        logger.info(f"بدء تحميل جميع الشهادات للعضو: {member_display_name}")
        self.member_processing_started_signal.emit(self.index) 
        self._emit_global_log(f"جاري تحميل شهادات...")

        all_downloads_successful = True 
        first_error_encountered = "" 
        aggregated_status_messages = [] 
        
        path_honneur_final = self.member.pdf_honneur_path 
        path_rdv_final = self.member.pdf_rdv_path       

        documents_location = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base_app_dir_name = "ملفات_المنحة_البرنامج"
        member_name_for_folder = self.member.get_full_name_ar()
        if not member_name_for_folder or member_name_for_folder.isspace(): 
            member_name_for_folder = self.member.nin 
        
        safe_folder_name_part = "".join(c for c in member_name_for_folder if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ", "_")
        if not safe_folder_name_part: safe_folder_name_part = self.member.nin 
        
        member_specific_output_dir = os.path.join(documents_location, base_app_dir_name, safe_folder_name_part)
        
        try:
            os.makedirs(member_specific_output_dir, exist_ok=True) 
            logger.info(f"تم إنشاء/التحقق من مجلد العضو: {member_specific_output_dir}")
        except Exception as e_mkdir:
            logger.error(f"فشل إنشاء مجلد للعضو {member_display_name}: {e_mkdir}")
            user_friendly_mkdir_error = f"فشل إنشاء مجلد لحفظ الملفات: {e_mkdir}"
            if self.is_running: self.all_pdfs_download_finished_signal.emit(self.index, None, None, user_friendly_mkdir_error, False, str(e_mkdir))
            if self.is_running: self.member_processing_finished_signal.emit(self.index)
            return

        if not self.is_running: self.member_processing_finished_signal.emit(self.index); return 
        fp_h, s_h, err_h, stat_h = self._download_single_pdf("HonneurEngagementReport", "التزام", member_specific_output_dir)
        aggregated_status_messages.append(stat_h)
        if s_h: path_honneur_final = fp_h
        else: all_downloads_successful = False; first_error_encountered = first_error_encountered or err_h 
        
        if self.is_running and (self.member.already_has_rdv or self.member.rdv_id): 
            fp_r, s_r, err_r, stat_r = self._download_single_pdf("RdvReport", "موعد", member_specific_output_dir)
            aggregated_status_messages.append(stat_r)
            if s_r: path_rdv_final = fp_r
            else: all_downloads_successful = False; first_error_encountered = first_error_encountered or err_r
        elif self.is_running: 
            msg_skip_rdv = "شهادة الموعد غير مطلوبة/متوفرة (لا يوجد موعد مسجل)."
            logger.info(msg_skip_rdv + f" للعضو {member_display_name}")
            aggregated_status_messages.append(msg_skip_rdv)
            if self.is_running: self.individual_pdf_status_signal.emit(self.index, "RdvReport", msg_skip_rdv, True, "") 

        final_overall_status_msg_for_signal = "; ".join(msg for msg in aggregated_status_messages if msg)
        if not all_downloads_successful and first_error_encountered:
            final_overall_status_msg_for_signal = f"فشل تحميل بعض الملفات. أول خطأ: {first_error_encountered.split(':')[0]}"
        elif all_downloads_successful:
             final_overall_status_msg_for_signal = "تم تحميل جميع الشهادات المطلوبة بنجاح."
        
        if self.is_running:
            self.all_pdfs_download_finished_signal.emit(self.index, path_honneur_final, path_rdv_final, final_overall_status_msg_for_signal, all_downloads_successful, first_error_encountered)
            self._emit_global_log(f"انتهاء تحميل شهادات. الحالة: {final_overall_status_msg_for_signal}")
        
        self.member_processing_finished_signal.emit(self.index) 
        logger.info(f"انتهاء تحميل جميع الشهادات للعضو: {member_display_name}. النجاح الكلي: {all_downloads_successful}")

    def stop(self): 
        self.is_running = False
        member_display_name = self._get_member_display_name_with_index_from_thread(self.member, self.index)
        logger.info(f"طلب إيقاف خيط تحميل جميع الشهادات للعضو: {member_display_name}")
