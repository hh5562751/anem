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

# مدة تأخير قصيرة جدًا بالثواني للأعضاء الذين يتم تخطيهم
SHORT_SKIP_DELAY_SECONDS = 0.1 

class FetchInitialInfoThread(QThread):
    # إشارة لتحديث واجهة المستخدم بمعلومات العضو
    update_member_gui_signal = pyqtSignal(int, str, str, str) 
    # إشارة لإرسال البيانات الجديدة التي تم جلبها (مثل الاسم)
    new_data_fetched_signal = pyqtSignal(int, str, str) 
    # إشارة لبدء معالجة عضو
    member_processing_started_signal = pyqtSignal(int) 
    # إشارة لانتهاء معالجة عضو
    member_processing_finished_signal = pyqtSignal(int) 

    def __init__(self, member, index, api_client, settings, parent=None): 
        super().__init__(parent)
        self.member = member 
        self.index = index
        self.api_client = api_client
        self.settings = settings # الإعدادات الحالية للتطبيق

    def run(self):
        logger.info(f"بدء جلب المعلومات الأولية للعضو: {self.member.nin}")
        self.member_processing_started_signal.emit(self.index) # إرسال إشارة بدء المعالجة
        # تحديث واجهة المستخدم بحالة "جاري التحقق الأولي"
        self.update_member_gui_signal.emit(self.index, "جاري التحقق الأولي...", f"بدء التحقق للعضو {self.member.nin}", get_icon_name_for_status("جاري التحقق الأولي..."))

        try:
            # تأخير عشوائي بسيط قبل بدء المعالجة لتجنب إرسال عدد كبير من الطلبات في نفس اللحظة
            initial_delay = random.uniform(0.5, 2.0) 
            logger.debug(f"FetchInitialInfoThread: تأخير عشوائي {initial_delay:.2f} ثانية قبل معالجة {self.member.nin}")
            time.sleep(initial_delay)

            # استدعاء الواجهة البرمجية للتحقق من صحة بيانات المرشح
            data_val, error_val = self.api_client.validate_candidate(self.member.wassit_no, self.member.nin)

            if error_val:
                self.member.status = "فشل التحقق الأولي"
                self.member.set_activity_detail(f"فشل التحقق: {error_val}", is_error=True)
            elif data_val:
                # تحديث معلومات الاستفادة من المنحة إذا كانت متوفرة
                self.member.have_allocation = data_val.get("haveAllocation", False)
                self.member.allocation_details = data_val.get("detailsAllocation", {})
                
                if self.member.have_allocation and self.member.allocation_details:
                    self.member.status = "مستفيد حاليًا من المنحة"
                    nom_ar = self.member.allocation_details.get("nomAr", "")
                    prenom_ar = self.member.allocation_details.get("prenomAr", "")
                    nom_fr = self.member.allocation_details.get("nomFr", "")
                    prenom_fr = self.member.allocation_details.get("prenomFr", "")
                    date_debut = self.member.allocation_details.get("dateDebut", "غير محدد")
                    if date_debut and "T" in date_debut: date_debut = date_debut.split("T")[0] # تنسيق التاريخ

                    # تحديث أسماء العضو وإرسال إشارة بالبيانات الجديدة
                    self.member.nom_ar = nom_ar
                    self.member.prenom_ar = prenom_ar
                    self.member.nom_fr = nom_fr
                    self.member.prenom_fr = prenom_fr
                    self.new_data_fetched_signal.emit(self.index, nom_ar, prenom_ar)
                    self.member.set_activity_detail(f"مستفيد حاليًا. تاريخ بدء الاستفادة: {date_debut}.")
                    logger.info(f"العضو {self.member.nin} ( {self.member.get_full_name_ar()} ) مستفيد حاليًا من المنحة، تاريخ البدء: {date_debut}.")
                else:
                    # معالجة الحالات الأخرى بناءً على استجابة التحقق
                    is_eligible_from_validate = data_val.get("eligible", False)
                    self.member.has_actual_pre_inscription = data_val.get("havePreInscription", False)
                    self.member.already_has_rdv = data_val.get("haveRendezVous", False)
                    valid_input = data_val.get("validInput", True)
                    self.member.pre_inscription_id = data_val.get("preInscriptionId")
                    self.member.demandeur_id = data_val.get("demandeurId")
                    self.member.structure_id = data_val.get("structureId")
                    self.member.rdv_id = data_val.get("rendezVousId") # قد يكون فارغًا

                    if not valid_input:
                        controls = data_val.get("controls", [])
                        error_msg_from_controls = "البيانات المدخلة غير متطابقة أو غير صالحة."
                        for control in controls: # البحث عن رسالة الخطأ المحددة
                            if control.get("result") is False and control.get("name") == "matchIdentity" and control.get("message"):
                                error_msg_from_controls = control.get("message")
                                break
                        self.member.status = "بيانات الإدخال خاطئة"
                        self.member.set_activity_detail(error_msg_from_controls, is_error=True)
                        logger.warning(f"خطأ في بيانات الإدخال للعضو {self.member.nin}: {error_msg_from_controls}")
                    elif self.member.already_has_rdv:
                        self.member.status = "لديه موعد مسبق"
                        activity_msg = f"لديه موعد محجوز بالفعل (ID: {self.member.rdv_id or 'N/A'})."
                        # محاولة جلب الاسم إذا لم يكن متوفرًا بالفعل
                        if self.member.pre_inscription_id and not (self.member.nom_ar and self.member.prenom_ar):
                            data_info, error_info = self.api_client.get_pre_inscription_info(self.member.pre_inscription_id)
                            if data_info:
                                self.member.nom_ar = data_info.get("nomDemandeurAr", "")
                                self.member.prenom_ar = data_info.get("prenomDemandeurAr", "")
                                self.member.nom_fr = data_info.get("nomDemandeurFr", "")
                                self.member.prenom_fr = data_info.get("prenomDemandeurFr", "")
                                self.new_data_fetched_signal.emit(self.index, self.member.nom_ar, self.member.prenom_ar)
                                activity_msg += f" الاسم: {self.member.get_full_name_ar()}"
                                logger.info(f"تم جلب الاسم واللقب للعضو {self.member.nin} الذي لديه موعد مسبق.")
                        self.member.set_activity_detail(activity_msg)
                    elif is_eligible_from_validate:
                        if self.member.has_actual_pre_inscription:
                            self.member.status = "تم التحقق"
                            self.member.set_activity_detail("مؤهل ولديه تسجيل مسبق. جاري جلب الاسم...")
                        else:
                            self.member.status = "يتطلب تسجيل مسبق"
                            self.member.set_activity_detail("مؤهل ولكن لا يوجد تسجيل مسبق بعد.")
                        
                        # محاولة جلب الاسم إذا كان التسجيل المسبق موجودًا والاسم غير متوفر
                        if self.member.pre_inscription_id and not (self.member.nom_ar and self.member.prenom_ar):
                            data_info, error_info = self.api_client.get_pre_inscription_info(self.member.pre_inscription_id)
                            if error_info:
                                self.member.status = "فشل جلب المعلومات" if self.member.status != "يتطلب تسجيل مسبق" else self.member.status
                                self.member.set_activity_detail(f"{self.member.last_activity_detail} فشل جلب الاسم: {error_info}".strip(), is_error=True)
                            elif data_info:
                                self.member.nom_ar = data_info.get("nomDemandeurAr", "")
                                self.member.prenom_ar = data_info.get("prenomDemandeurAr", "")
                                self.member.nom_fr = data_info.get("nomDemandeurFr", "")
                                self.member.prenom_fr = data_info.get("prenomDemandeurFr", "")
                                self.new_data_fetched_signal.emit(self.index, self.member.nom_ar, self.member.prenom_ar)
                                self.member.status = "تم جلب المعلومات" if self.member.status == "تم التحقق" else self.member.status # تحديث الحالة إذا تم جلب الاسم بنجاح
                                self.member.set_activity_detail(f"تم جلب الاسم: {self.member.get_full_name_ar()}")
                                logger.info(f"تم جلب الاسم واللقب للعضو {self.member.nin}: ع ({self.member.get_full_name_ar()}), ف ({self.member.nom_fr} {self.member.prenom_fr})")
                    else: # غير مؤهل
                        self.member.status = "غير مؤهل مبدئيًا"
                        self.member.set_activity_detail(str(data_val.get("message", "المترشح غير مؤهل.")), is_error=True)
                        logger.warning(f"العضو {self.member.nin} غير مؤهل مبدئيًا: {self.member.full_last_activity_detail}")
            else: # استجابة فارغة من الخادم
                self.member.status = "فشل التحقق الأولي"
                self.member.set_activity_detail("استجابة فارغة عند التحقق", is_error=True)
        except Exception as e:
            logger.exception(f"خطأ غير متوقع في FetchInitialInfoThread للعضو {self.member.nin}: {e}")
            self.member.status = "خطأ في الجلب الأولي"
            self.member.set_activity_detail(str(e), is_error=True)
        finally:
            # التأكد من إرسال إشارة انتهاء المعالجة وتحديث واجهة المستخدم بالحالة النهائية
            final_icon = get_icon_name_for_status(self.member.status)
            self.update_member_gui_signal.emit(self.index, self.member.status, self.member.last_activity_detail, final_icon)
            self.member_processing_finished_signal.emit(self.index) 


class MonitoringThread(QThread):
    # إشارات لتحديث واجهة المستخدم والاتصال مع الخيط الرئيسي
    update_member_gui_signal = pyqtSignal(int, str, str, str) # (index, status, detail, icon_name)
    new_data_fetched_signal = pyqtSignal(int, str, str)      # (index, nom_ar, prenom_ar)
    global_log_signal = pyqtSignal(str)                      # رسالة عامة لشريط الحالة
    member_being_processed_signal = pyqtSignal(int, bool)    # (index, is_processing)

    SITE_CHECK_INTERVAL_SECONDS = 60 # الفاصل الزمني لفحص توفر الموقع عند فقدان الاتصال
    MAX_CONSECUTIVE_MEMBER_FAILURES = 5 # أقصى عدد محاولات فاشلة متتالية لعضو واحد قبل تجاهله مؤقتًا
    CONSECUTIVE_NETWORK_ERROR_THRESHOLD = 3 # عدد أخطاء الشبكة المتتالية التي تؤدي إلى وضع فحص الاتصال

    def __init__(self, members_list_ref, settings):
        super().__init__()
        self.members_list_ref = members_list_ref # مرجع لقائمة الأعضاء الرئيسية
        self.settings = settings.copy() # نسخة من إعدادات التطبيق
        self._apply_settings() # تطبيق الإعدادات على الخيط

        self.is_running = True # علم للتحكم في استمرار عمل الخيط
        self.is_connection_lost_mode = False # علم لتحديد ما إذا كان الاتصال بالخادم مفقودًا
        self.current_member_index_to_process = 0 # مؤشر للعضو التالي الذي سيتم معالجته
        self.consecutive_network_error_trigger_count = 0 # عداد لأخطاء الشبكة المتتالية
        self.initial_scan_completed = False # علم لتتبع اكتمال الفحص الأولي

    def _apply_settings(self):
        # تطبيق الإعدادات على متغيرات الخيط
        self.interval_ms = self.settings.get(SETTING_MONITORING_INTERVAL, DEFAULT_SETTINGS[SETTING_MONITORING_INTERVAL]) * 60 * 1000
        self.min_member_delay = self.settings.get(SETTING_MIN_MEMBER_DELAY, DEFAULT_SETTINGS[SETTING_MIN_MEMBER_DELAY])
        self.max_member_delay = self.settings.get(SETTING_MAX_MEMBER_DELAY, DEFAULT_SETTINGS[SETTING_MAX_MEMBER_DELAY])
        
        # إعادة تهيئة عميل الواجهة البرمجية بالإعدادات الجديدة
        self.api_client = AnemAPIClient(
            initial_backoff_general=self.settings.get(SETTING_BACKOFF_GENERAL, DEFAULT_SETTINGS[SETTING_BACKOFF_GENERAL]),
            initial_backoff_429=self.settings.get(SETTING_BACKOFF_429, DEFAULT_SETTINGS[SETTING_BACKOFF_429]),
            request_timeout=self.settings.get(SETTING_REQUEST_TIMEOUT, DEFAULT_SETTINGS[SETTING_REQUEST_TIMEOUT])
        )
        logger.info(f"MonitoringThread settings applied: Interval={self.interval_ms/60000:.1f}min, MemberDelay=[{self.min_member_delay}-{self.max_member_delay}]s")

    def update_thread_settings(self, new_settings):
        # تحديث إعدادات الخيط أثناء عمله
        logger.info("MonitoringThread: استلام طلب تحديث الإعدادات.")
        self.settings = new_settings.copy()
        self._apply_settings()

    def run(self):
        # الدالة الرئيسية لعمل الخيط

        # تعريف قوائم الحالات هنا لتكون متاحة للفحص الأولي والدوري
        statuses_to_completely_skip_monitoring = ["مستفيد حاليًا من المنحة"]
        statuses_for_pdf_check_only = ["مكتمل", "لديه موعد مسبق", "غير مؤهل مبدئيًا", "غير مؤهل للحجز", "بيانات الإدخال خاطئة"]

        while self.is_running:
            if self.is_connection_lost_mode:
                self.global_log_signal.emit(f"الاتصال بالخادم مفقود. جاري فحص توفر الموقع...")
                site_available = self.api_client.check_main_site_availability()
                if site_available:
                    logger.info("تم استعادة الاتصال بالخادم الرئيسي. استئناف المراقبة.")
                    self.global_log_signal.emit("تم استعادة الاتصال بالخادم. استئناف المراقبة.")
                    self.is_connection_lost_mode = False
                    self.consecutive_network_error_trigger_count = 0 
                    logger.info("إعادة تعيين عداد الفشل المتتالي لجميع الأعضاء بعد استعادة الاتصال.")
                    for member_to_reset in self.members_list_ref:
                        member_to_reset.consecutive_failures = 0
                    # إذا لم يتم الفحص الأولي بعد، سيتم إجراؤه في الدورة التالية
                    continue 
                else:
                    logger.info(f"الموقع الرئيسي لا يزال غير متاح. الفحص التالي بعد {self.SITE_CHECK_INTERVAL_SECONDS} ثانية.")
                    self.global_log_signal.emit(f"الموقع لا يزال غير متاح. الفحص التالي بعد {self.SITE_CHECK_INTERVAL_SECONDS} ثانية.")
                    for i in range(self.SITE_CHECK_INTERVAL_SECONDS):
                        if not self.is_running: break
                        time.sleep(1)
                    if not self.is_running: break
                    continue 

            # --- الفحص الأولي الشامل عند بدء المراقبة لأول مرة ---
            if self.is_running and not self.initial_scan_completed and not self.is_connection_lost_mode:
                logger.info("بدء الفحص الأولي لجميع الأعضاء عند بدء المراقبة...")
                self.global_log_signal.emit("جاري الفحص الأولي لجميع الأعضاء...")
                
                initial_scan_members_list = list(self.members_list_ref) # أخذ نسخة لتجنب مشاكل التعديل أثناء التكرار

                if not initial_scan_members_list:
                    logger.info("الفحص الأولي: لا يوجد أعضاء للفحص.")
                    self.global_log_signal.emit("الفحص الأولي: لا يوجد أعضاء.")
                else:
                    for initial_scan_idx, member_to_process in enumerate(initial_scan_members_list):
                        if not self.is_running: break
                        
                        # التأكد من أن العضو لا يزال موجودًا في القائمة الأصلية (قد يتم حذفه)
                        # هذا الفحص أقل أهمية هنا لأننا نكرر على نسخة، ولكن جيد للحذر
                        if initial_scan_idx >= len(self.members_list_ref) or self.members_list_ref[initial_scan_idx] != member_to_process :
                             logger.warning(f"الفحص الأولي: تجاوز العضو (فهرس أصلي محتمل {initial_scan_idx}) لأنه تغير أو لم يعد موجودًا.")
                             continue

                        if member_to_process.is_processing:
                            logger.debug(f"الفحص الأولي: تجاوز العضو {member_to_process.nin} (فهرس {initial_scan_idx}) لأنه قيد المعالجة.")
                            continue

                        if member_to_process.consecutive_failures >= self.MAX_CONSECUTIVE_MEMBER_FAILURES:
                            if "فشل بشكل متكرر" not in member_to_process.status:
                                logger.warning(f"الفحص الأولي: تجاوز العضو {member_to_process.nin} بسبب {member_to_process.consecutive_failures} محاولات فاشلة.")
                                member_to_process.status = "فشل بشكل متكرر"
                                member_to_process.set_activity_detail(f"تم تجاوز العضو بسبب {member_to_process.consecutive_failures} محاولات فاشلة.", is_error=True)
                                self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                            continue
                        
                        if member_to_process.status in statuses_to_completely_skip_monitoring:
                            logger.info(f"الفحص الأولي: تجاوز العضو {member_to_process.nin} لأنه في حالة: {member_to_process.status}.")
                            self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                            self.member_being_processed_signal.emit(initial_scan_idx, False)
                            if self.is_running: time.sleep(SHORT_SKIP_DELAY_SECONDS)
                            continue

                        self.member_being_processed_signal.emit(initial_scan_idx, True)
                        logger.info(f"الفحص الأولي للعضو (فهرس {initial_scan_idx}): {member_to_process.nin} - الحالة الحالية: {member_to_process.status}")
                        self.global_log_signal.emit(f"فحص أولي: {member_to_process.get_full_name_ar() or member_to_process.nin} ({member_to_process.nin})")
                        
                        member_had_api_error_this_cycle = False
                        try:
                            if member_to_process.status in statuses_for_pdf_check_only:
                                logger.info(f"الفحص الأولي: العضو {member_to_process.nin} ({member_to_process.status})، فحص PDF فقط.")
                                if member_to_process.pre_inscription_id:
                                    _, api_error_occurred_pdf = self.process_pdf_download(initial_scan_idx, member_to_process)
                                    if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                                else:
                                    member_to_process.set_activity_detail("الفحص الأولي: لا يمكن تحميل PDF، ID التسجيل مفقود.", is_error=True)
                            else:
                                validation_success, api_error_occurred_validation = self.process_validation(initial_scan_idx, member_to_process)
                                if api_error_occurred_validation: member_had_api_error_this_cycle = True
                                if not self.is_running: break

                                is_in_final_state_after_validation = member_to_process.status in [
                                    "مستفيد حاليًا من المنحة", "غير مؤهل مبدئيًا", "بيانات الإدخال خاطئة", 
                                    "لديه موعد مسبق", "غير مؤهل للحجز", "فشل التحقق"
                                ]

                                if not is_in_final_state_after_validation and validation_success:
                                    if member_to_process.pre_inscription_id and not (member_to_process.nom_ar and member_to_process.prenom_ar):
                                        if not self.is_running: break
                                        _, api_error_occurred_info = self.process_pre_inscription_info(initial_scan_idx, member_to_process)
                                        if api_error_occurred_info: member_had_api_error_this_cycle = True
                                        if not self.is_running or "فشل جلب" in member_to_process.status: pass

                                    if not self.is_running: break
                                    if member_to_process.status in ["تم جلب المعلومات", "تم التحقق", "لا توجد مواعيد", "فشل جلب التواريخ", "يتطلب تسجيل مسبق"] and \
                                       member_to_process.has_actual_pre_inscription and member_to_process.pre_inscription_id and \
                                       member_to_process.demandeur_id and member_to_process.structure_id and \
                                       not member_to_process.already_has_rdv and not member_to_process.have_allocation:
                                        _, api_error_occurred_booking = self.process_available_dates_and_book(initial_scan_idx, member_to_process)
                                        if api_error_occurred_booking: member_had_api_error_this_cycle = True
                                        if not self.is_running or member_to_process.status in ["فشل الحجز", "غير مؤهل للحجز"]: pass
                            
                            pdf_attempt_worthy_statuses_after_processing = ["تم الحجز", "مكتمل", "فشل تحميل PDF"] 
                            if member_to_process.status in pdf_attempt_worthy_statuses_after_processing and member_to_process.pre_inscription_id:
                                if not self.is_running: break
                                logger.info(f"الفحص الأولي: العضو {member_to_process.nin} ({member_to_process.status}) يستدعي محاولة تحميل PDF.")
                                _, api_error_occurred_pdf = self.process_pdf_download(initial_scan_idx, member_to_process)
                                if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                            
                            if member_had_api_error_this_cycle:
                                member_to_process.consecutive_failures += 1
                                self.consecutive_network_error_trigger_count +=1 
                            else:
                                member_to_process.consecutive_failures = 0
                                # لا نعيد تعيين عداد الشبكة العام هنا إلا إذا نجح عضو،
                                # ولكن إذا كان الفحص الأولي يفشل بشكل متكرر، قد ندخل وضع فقدان الاتصال.
                                if not member_had_api_error_this_cycle : self.consecutive_network_error_trigger_count = 0


                        except Exception as e:
                            logger.exception(f"الفحص الأولي: خطأ غير متوقع للعضو {member_to_process.nin}: {e}")
                            member_to_process.status = "خطأ في المعالجة"
                            member_to_process.set_activity_detail(f"خطأ غير متوقع: {str(e)}", is_error=True)
                            member_to_process.consecutive_failures +=1
                            self.consecutive_network_error_trigger_count +=1
                            self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, "SP_MessageBoxCritical")
                        finally:
                            self.member_being_processed_signal.emit(initial_scan_idx, False)
                            self.update_member_gui_signal.emit(initial_scan_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))

                        if not self.is_running: break
                        if self.consecutive_network_error_trigger_count >= self.CONSECUTIVE_NETWORK_ERROR_THRESHOLD:
                            logger.warning(f"الفحص الأولي: {self.consecutive_network_error_trigger_count} أعضاء متتاليين واجهوا أخطاء شبكة. الدخول في وضع فحص الاتصال.")
                            self.global_log_signal.emit("الفحص الأولي: أخطاء شبكة متتالية. إيقاف مؤقت.")
                            self.is_connection_lost_mode = True
                            break # الخروج من حلقة الفحص الأولي

                        member_delay = random.uniform(self.min_member_delay, self.max_member_delay)
                        logger.info(f"الفحص الأولي: تأخير {member_delay:.2f} ثانية قبل العضو التالي.")
                        for _ in range(int(member_delay)):
                            if not self.is_running: break
                            time.sleep(1)
                        if not self.is_running: break
                        if self.is_running:
                            time.sleep(member_delay - int(member_delay))
                    
                    if self.is_connection_lost_mode: # إذا دخلنا في وضع فقدان الاتصال أثناء الفحص الأولي
                        continue # العودة لبداية الحلقة الرئيسية للتعامل مع فقدان الاتصال

                self.initial_scan_completed = True
                self.current_member_index_to_process = 0 # البدء من جديد للدورة الدورية
                logger.info("اكتمل الفحص الأولي لجميع الأعضاء.")
                self.global_log_signal.emit("اكتمل الفحص الأولي. بدء المراقبة الدورية...")
            # --- نهاية الفحص الأولي ---

            if not self.is_running: break # تحقق قبل بدء الدورة الدورية

            # --- بدء المراقبة الدورية العادية ---
            current_members_snapshot_indices = list(range(len(self.members_list_ref)))

            if not current_members_snapshot_indices: 
                logger.info("المراقبة الدورية: لا يوجد أعضاء للمراقبة.")
                self.global_log_signal.emit("لا يوجد أعضاء للمراقبة الدورية.")
                for _ in range(int(min(self.interval_ms / 1000, 30))): 
                    if not self.is_running: break
                    time.sleep(1)
                if not self.is_running: break
                continue 

            logger.info(f"بدء دورة مراقبة دورية... (من الفهرس {self.current_member_index_to_process}) عدد الأعضاء الكلي: {len(current_members_snapshot_indices)}")
            self.global_log_signal.emit(f"بدء دورة مراقبة دورية... ({time.strftime('%H:%M:%S')})")

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

                if member_to_process.is_processing: 
                    logger.debug(f"المراقبة الدورية: تجاوز العضو {member_to_process.nin} (فهرس {main_list_idx}) لأنه قيد المعالجة.")
                    continue

                if member_to_process.consecutive_failures >= self.MAX_CONSECUTIVE_MEMBER_FAILURES:
                    if "فشل بشكل متكرر" not in member_to_process.status : 
                        logger.warning(f"المراقبة الدورية: تجاوز العضو {member_to_process.nin} بسبب {member_to_process.consecutive_failures} محاولات فاشلة.")
                        member_to_process.status = "فشل بشكل متكرر"
                        member_to_process.set_activity_detail(f"تم تجاوز العضو بسبب {member_to_process.consecutive_failures} محاولات فاشلة.", is_error=True)
                        self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                    continue 
                
                if member_to_process.status in statuses_to_completely_skip_monitoring:
                    logger.info(f"المراقبة الدورية: تجاوز العضو {member_to_process.nin} لأنه في حالة: {member_to_process.status}.")
                    self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))
                    self.member_being_processed_signal.emit(main_list_idx, False) 
                    if self.is_running: time.sleep(SHORT_SKIP_DELAY_SECONDS)
                    self.current_member_index_to_process = (main_list_idx + 1) % len(self.members_list_ref) if self.members_list_ref else 0
                    continue 

                self.member_being_processed_signal.emit(main_list_idx, True) 
                
                logger.info(f"المراقبة الدورية: فحص العضو (فهرس {main_list_idx}): {member_to_process.nin} - الحالة: {member_to_process.status}")
                self.global_log_signal.emit(f"جاري فحص دوري: {member_to_process.get_full_name_ar()} ({member_to_process.nin})")
                
                processed_in_this_cycle = True 
                member_had_api_error_this_cycle = False 

                try:
                    if member_to_process.status in statuses_for_pdf_check_only:
                        logger.info(f"المراقبة الدورية: العضو {member_to_process.nin} ({member_to_process.status})، فحص PDF فقط.")
                        if member_to_process.pre_inscription_id: 
                            pdf_success, api_error_occurred_pdf = self.process_pdf_download(main_list_idx, member_to_process)
                            if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                        else:
                            member_to_process.set_activity_detail("المراقبة الدورية: لا يمكن تحميل PDF، ID التسجيل مفقود.", is_error=True)
                    else: 
                        validation_success, api_error_occurred_validation = self.process_validation(main_list_idx, member_to_process)
                        if api_error_occurred_validation: member_had_api_error_this_cycle = True
                        if not self.is_running: break

                        is_in_final_state_after_validation = member_to_process.status in [
                            "مستفيد حاليًا من المنحة", "غير مؤهل مبدئيًا", "بيانات الإدخال خاطئة", 
                            "لديه موعد مسبق", "غير مؤهل للحجز", "فشل التحقق"
                        ]

                        if not is_in_final_state_after_validation and validation_success:
                            if member_to_process.pre_inscription_id and not (member_to_process.nom_ar and member_to_process.prenom_ar):
                                if not self.is_running: break
                                info_success, api_error_occurred_info = self.process_pre_inscription_info(main_list_idx, member_to_process)
                                if api_error_occurred_info: member_had_api_error_this_cycle = True
                                if not self.is_running or "فشل جلب" in member_to_process.status: pass 

                            if not self.is_running: break
                            if member_to_process.status in ["تم جلب المعلومات", "تم التحقق", "لا توجد مواعيد", "فشل جلب التواريخ", "يتطلب تسجيل مسبق"] and \
                               member_to_process.has_actual_pre_inscription and member_to_process.pre_inscription_id and \
                               member_to_process.demandeur_id and member_to_process.structure_id and \
                               not member_to_process.already_has_rdv and not member_to_process.have_allocation:
                                booking_success, api_error_occurred_booking = self.process_available_dates_and_book(main_list_idx, member_to_process)
                                if api_error_occurred_booking: member_had_api_error_this_cycle = True
                                if not self.is_running or member_to_process.status in ["فشل الحجز", "غير مؤهل للحجز"]: pass 
                    
                    pdf_attempt_worthy_statuses_after_processing = ["تم الحجز", "مكتمل", "فشل تحميل PDF"] 
                    if member_to_process.status in pdf_attempt_worthy_statuses_after_processing and member_to_process.pre_inscription_id:
                        if not self.is_running: break
                        logger.info(f"المراقبة الدورية: العضو {member_to_process.nin} ({member_to_process.status}) يستدعي محاولة تحميل PDF.")
                        pdf_success, api_error_occurred_pdf = self.process_pdf_download(main_list_idx, member_to_process)
                        if api_error_occurred_pdf: member_had_api_error_this_cycle = True
                    
                    if member_had_api_error_this_cycle:
                        member_to_process.consecutive_failures += 1
                        self.consecutive_network_error_trigger_count +=1 
                    else: 
                        member_to_process.consecutive_failures = 0
                        self.consecutive_network_error_trigger_count = 0 

                except Exception as e:
                    logger.exception(f"المراقبة الدورية: خطأ غير متوقع للعضو {member_to_process.nin}: {e}")
                    member_to_process.status = "خطأ في المعالجة"
                    member_to_process.set_activity_detail(f"خطأ غير متوقع: {str(e)}", is_error=True)
                    member_to_process.consecutive_failures +=1 
                    self.consecutive_network_error_trigger_count +=1 
                    self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, "SP_MessageBoxCritical")
                finally:
                    self.member_being_processed_signal.emit(main_list_idx, False) 
                    self.update_member_gui_signal.emit(main_list_idx, member_to_process.status, member_to_process.last_activity_detail, get_icon_name_for_status(member_to_process.status))

                if not self.is_running: break 

                if self.consecutive_network_error_trigger_count >= self.CONSECUTIVE_NETWORK_ERROR_THRESHOLD:
                    logger.warning(f"المراقبة الدورية: {self.consecutive_network_error_trigger_count} أعضاء متتاليين واجهوا أخطاء شبكة. الدخول في وضع فحص الاتصال.")
                    self.global_log_signal.emit("أخطاء شبكة متتالية. إيقاف مؤقت للمراقبة الدورية.")
                    self.is_connection_lost_mode = True
                    break 

                member_delay = random.uniform(self.min_member_delay, self.max_member_delay)
                logger.info(f"المراقبة الدورية: تأخير {member_delay:.2f} ثانية قبل العضو التالي.")
                for _ in range(int(member_delay)): 
                    if not self.is_running: break
                    time.sleep(1)
                if not self.is_running: break
                if self.is_running: 
                    time.sleep(member_delay - int(member_delay))

                self.current_member_index_to_process = (main_list_idx + 1) % len(self.members_list_ref) if self.members_list_ref else 0

            if not self.is_running: break 
            if self.is_connection_lost_mode: continue 

            self.current_member_index_to_process = 0 

            if processed_in_this_cycle:
                logger.info(f"إكمال دورة مراقبة دورية. الدورة القادمة بعد {self.interval_ms / 60000:.1f} دقيقة.")
                self.global_log_signal.emit(f"انتهاء دورة المراقبة الدورية. الدورة القادمة بعد {self.interval_ms / 60000:.1f} دقيقة.")
            else: 
                logger.info(f"المراقبة الدورية: لم يتم فحص أي أعضاء. الانتظار للدورة القادمة.")
                self.global_log_signal.emit("المراقبة الدورية: لم يتم فحص أي أعضاء مؤهلين.")

            for _ in range(int(self.interval_ms / 1000)): 
                if not self.is_running: break
                time.sleep(1)
            if not self.is_running: break
        
        logger.info("خيط المراقبة يتوقف.")

    def _update_member_and_emit(self, main_list_idx, member_obj_being_updated, new_status, detail_text, icon_name):
        # دالة مساعدة لتحديث بيانات العضو وإرسال إشارة لواجهة المستخدم
        member_obj_being_updated.status = new_status
        # تحديد ما إذا كانت الرسالة خطأ لتمريرها إلى set_activity_detail
        is_error_flag = "فشل" in new_status or "خطأ" in new_status or "غير مؤهل" in new_status or "بيانات الإدخال خاطئة" in new_status
        member_obj_being_updated.set_activity_detail(detail_text, is_error=is_error_flag)
        logger.info(f"تحديث حالة العضو {member_obj_being_updated.nin} (فهرس رئيسي {main_list_idx}): {new_status} - التفاصيل: {member_obj_being_updated.last_activity_detail}")
        self.update_member_gui_signal.emit(main_list_idx, member_obj_being_updated.status, member_obj_being_updated.last_activity_detail, icon_name)

    def process_validation(self, main_list_idx, member_obj): 
        # معالجة التحقق من صحة بيانات المرشح
        self._update_member_and_emit(main_list_idx, member_obj, "جاري التحقق (دورة)...", f"إعادة التحقق للعضو {member_obj.nin}", get_icon_name_for_status("جاري التحقق (دورة)..."))
        data, error = self.api_client.validate_candidate(member_obj.wassit_no, member_obj.nin)
        
        new_status = member_obj.status # الحالة الافتراضية هي الحالة الحالية
        validation_can_progress = False # علم لتحديد ما إذا كان يمكن المتابعة إلى الخطوات التالية
        api_error_occurred = False # علم لتتبع أخطاء الشبكة
        detail_text_for_gui = member_obj.last_activity_detail # التفاصيل الافتراضية هي التفاصيل الحالية

        if error:
            new_status = "فشل التحقق"
            detail_text_for_gui = f"فشل التحقق: {error}"
            api_error_occurred = True
        elif data:
            # تحديث معلومات الاستفادة من المنحة
            member_obj.have_allocation = data.get("haveAllocation", False)
            member_obj.allocation_details = data.get("detailsAllocation", {})

            if member_obj.have_allocation and member_obj.allocation_details:
                new_status = "مستفيد حاليًا من المنحة"
                # استخلاص وتحديث بيانات الاسم والتاريخ
                nom_ar = member_obj.allocation_details.get("nomAr", member_obj.nom_ar) 
                prenom_ar = member_obj.allocation_details.get("prenomAr", member_obj.prenom_ar)
                nom_fr = member_obj.allocation_details.get("nomFr", member_obj.nom_fr)
                prenom_fr = member_obj.allocation_details.get("prenomFr", member_obj.prenom_fr)
                date_debut = member_obj.allocation_details.get("dateDebut", "غير محدد")
                if date_debut and "T" in date_debut: date_debut = date_debut.split("T")[0]

                if nom_ar != member_obj.nom_ar or prenom_ar != member_obj.prenom_ar: # إذا تغير الاسم
                    member_obj.nom_ar = nom_ar
                    member_obj.prenom_ar = prenom_ar
                    member_obj.nom_fr = nom_fr
                    member_obj.prenom_fr = prenom_fr
                    self.new_data_fetched_signal.emit(main_list_idx, nom_ar, prenom_ar) # إرسال إشارة بالاسم الجديد
                
                detail_text_for_gui = f"مستفيد حاليًا. تاريخ بدء الاستفادة: {date_debut}."
                validation_can_progress = False # لا يمكن المتابعة إذا كان مستفيدًا بالفعل
            else: # إذا لم يكن مستفيدًا، تحقق من الحالات الأخرى
                member_obj.has_actual_pre_inscription = data.get("havePreInscription", False)
                member_obj.already_has_rdv = data.get("haveRendezVous", False)
                valid_input = data.get("validInput", True)
                member_obj.pre_inscription_id = data.get("preInscriptionId")
                member_obj.demandeur_id = data.get("demandeurId")
                member_obj.structure_id = data.get("structureId")
                member_obj.rdv_id = data.get("rendezVousId") 

                if not valid_input:
                    controls = data.get("controls", [])
                    error_msg_from_controls = "البيانات المدخلة غير متطابقة أو غير صالحة."
                    for control in controls:
                        if control.get("result") is False and control.get("name") == "matchIdentity" and control.get("message"):
                            error_msg_from_controls = control.get("message")
                            break
                    new_status = "بيانات الإدخال خاطئة"
                    detail_text_for_gui = error_msg_from_controls
                elif member_obj.already_has_rdv:
                    new_status = "لديه موعد مسبق"
                    detail_text_for_gui = f"لديه موعد محجوز بالفعل (ID: {member_obj.rdv_id or 'N/A'})."
                    if member_obj.pre_inscription_id and not (member_obj.nom_ar and member_obj.prenom_ar):
                        validation_can_progress = True # يمكن محاولة جلب الاسم
                    else:
                        validation_can_progress = False # لا يمكن المتابعة إذا كان لديه موعد بالفعل والاسم معروف
                elif data.get("eligible", False) and member_obj.has_actual_pre_inscription:
                    new_status = "تم التحقق" 
                    detail_text_for_gui = "تم التحقق بنجاح (دورة)."
                    validation_can_progress = True
                elif data.get("eligible", False) and not member_obj.has_actual_pre_inscription:
                    new_status = "يتطلب تسجيل مسبق" 
                    detail_text_for_gui = "مؤهل ولكن لا يوجد تسجيل مسبق بعد (بانتظار توفر موعد)."
                    validation_can_progress = True 
                elif not data.get("eligible", False):
                    new_status = "غير مؤهل مبدئيًا"
                    detail_text_for_gui = str(data.get("message", "المترشح غير مؤهل (دورة)."))
                else: # حالة غير متوقعة
                    new_status = "فشل التحقق" 
                    detail_text_for_gui = "حالة غير معروفة بعد التحقق."
                    api_error_occurred = True
        else: # استجابة فارغة
            new_status = "فشل التحقق"
            detail_text_for_gui = "استجابة فارغة من الخادم عند التحقق."
            api_error_occurred = True
        
        icon = get_icon_name_for_status(new_status) 
        self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, icon)
        return validation_can_progress, api_error_occurred

    def process_pre_inscription_info(self, main_list_idx, member_obj): 
        # معالجة جلب معلومات التسجيل المسبق (الاسم)
        if not member_obj.pre_inscription_id:
            detail_text = "ID التسجيل المسبق غير متوفر لجلب الاسم."
            self._update_member_and_emit(main_list_idx, member_obj, member_obj.status, detail_text, get_icon_name_for_status(member_obj.status))
            return False, False # لا يمكن المتابعة، لا يعتبر خطأ API إذا كان ID مفقودًا من البداية
        
        self._update_member_and_emit(main_list_idx, member_obj, "جاري جلب الاسم...", f"محاولة جلب الاسم واللقب للعضو {member_obj.nin}", get_icon_name_for_status("جاري جلب الاسم..."))
        data, error = self.api_client.get_pre_inscription_info(member_obj.pre_inscription_id)
        
        new_status = member_obj.status # الحالة الافتراضية
        icon = get_icon_name_for_status(new_status)
        info_fetched_successfully = False
        api_error_occurred = False
        detail_text_for_gui = member_obj.last_activity_detail

        if error:
            if "جاري جلب الاسم..." in new_status : new_status = "فشل جلب المعلومات" # تحديث الحالة إذا كان الخطأ أثناء الجلب
            detail_text_for_gui = f"فشل جلب الاسم: {error}"
            api_error_occurred = True
        elif data:
            member_obj.nom_fr = data.get("nomDemandeurFr", "")
            member_obj.prenom_fr = data.get("prenomDemandeurFr", "")
            member_obj.nom_ar = data.get("nomDemandeurAr", "")
            member_obj.prenom_ar = data.get("prenomDemandeurAr", "")
            
            # تحديث الحالة بناءً على نتيجة جلب الاسم والحالة السابقة
            if "جاري جلب الاسم..." in new_status: 
                if member_obj.already_has_rdv: # إذا كان لديه موعد بالفعل
                    new_status = "لديه موعد مسبق" # تبقى الحالة كما هي
                    detail_text_for_gui = f"لديه موعد محجوز بالفعل. الاسم: {member_obj.get_full_name_ar()}"
                else: # إذا لم يكن لديه موعد
                    new_status = "تم جلب المعلومات" 
                    detail_text_for_gui = f"تم جلب الاسم: {member_obj.get_full_name_ar()}"
            elif member_obj.status == "لديه موعد مسبق": # إذا كانت الحالة الأصلية هي "لديه موعد مسبق"
                 detail_text_for_gui = f"لديه موعد محجوز بالفعل. الاسم: {member_obj.get_full_name_ar()}"
            else: # حالات أخرى (مثل "تم التحقق")
                 new_status = "تم جلب المعلومات"
                 detail_text_for_gui = f"تم جلب الاسم: {member_obj.get_full_name_ar()}"

            self.new_data_fetched_signal.emit(main_list_idx, member_obj.nom_ar, member_obj.prenom_ar) # إرسال إشارة بالاسم الجديد
            info_fetched_successfully = True
        else: # استجابة فارغة
            if "جاري جلب الاسم..." in new_status : new_status = "فشل جلب المعلومات"
            detail_text_for_gui = "استجابة فارغة عند جلب الاسم (دورة)."
            api_error_occurred = True 
        
        icon = get_icon_name_for_status(new_status)
        self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, icon)
        return info_fetched_successfully, api_error_occurred


    def process_available_dates_and_book(self, main_list_idx, member_obj): 
        # معالجة البحث عن مواعيد متاحة ومحاولة الحجز
        if not (member_obj.structure_id and member_obj.pre_inscription_id and member_obj.demandeur_id and member_obj.has_actual_pre_inscription):
            detail_text = "معلومات ناقصة أو التسجيل المسبق غير مؤكد لمحاولة الحجز."
            self._update_member_and_emit(main_list_idx, member_obj, member_obj.status, detail_text, get_icon_name_for_status(member_obj.status))
            return False, False # لا يمكن المتابعة، لا يعتبر خطأ API إذا كانت البيانات ناقصة من البداية
        
        self._update_member_and_emit(main_list_idx, member_obj, "جاري البحث عن مواعيد...", f"البحث عن مواعيد للعضو {member_obj.nin}", get_icon_name_for_status("جاري البحث عن مواعيد..."))
        data, error = self.api_client.get_available_dates(member_obj.structure_id, member_obj.pre_inscription_id)
        
        new_status = member_obj.status
        icon = get_icon_name_for_status(new_status)
        booking_successful = False
        api_error_occurred_this_stage = False 
        detail_text_for_gui = member_obj.last_activity_detail

        if error:
            new_status = "فشل جلب التواريخ"
            detail_text_for_gui = f"فشل جلب التواريخ: {error}"
            api_error_occurred_this_stage = True
        elif data and "dates" in data:
            available_dates = data["dates"]
            if available_dates:
                selected_date_str = available_dates[0] # اختيار أول تاريخ متاح
                try:
                    # تحويل تنسيق التاريخ
                    day, month, year = selected_date_str.split('/')
                    formatted_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}" 
                except ValueError:
                    new_status = "خطأ في تنسيق التاريخ"
                    detail_text_for_gui = f"تنسيق تاريخ غير صالح من الخادم: {selected_date_str}"
                    api_error_occurred_this_stage = True # يعتبر خطأ في البيانات المستلمة
                    self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, get_icon_name_for_status(new_status))
                    return False, api_error_occurred_this_stage
                
                self._update_member_and_emit(main_list_idx, member_obj, "جاري حجز الموعد...", f"محاولة الحجز في {formatted_date}", get_icon_name_for_status("جاري حجز الموعد..."))
                # التأكد من وجود البيانات المطلوبة للحجز
                if not (member_obj.ccp and member_obj.nom_fr and member_obj.prenom_fr):
                    new_status = "فشل الحجز"
                    detail_text_for_gui = "معلومات CCP أو الاسم الفرنسي مفقودة للحجز."
                    self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, get_icon_name_for_status(new_status))
                    return False, False # لا يمكن المتابعة، لا يعتبر خطأ API إذا كانت البيانات ناقصة
                
                # محاولة إنشاء الموعد
                book_data, book_error = self.api_client.create_rendezvous(
                    member_obj.pre_inscription_id, member_obj.ccp, member_obj.nom_fr, member_obj.prenom_fr,
                    formatted_date, member_obj.demandeur_id
                )

                if book_error: 
                    new_status = "فشل الحجز"
                    detail_text_for_gui = f"فشل الحجز: {book_error}"
                    api_error_occurred_this_stage = True
                elif book_data: 
                    if isinstance(book_data, dict) and book_data.get("Eligible") is False:
                        new_status = "غير مؤهل للحجز"
                        detail_text_for_gui = book_data.get("message", "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة.")
                        logger.warning(f"العضو {member_obj.nin} غير مؤهل للحجز حسب استجابة الخادم: {book_data}")
                    elif isinstance(book_data, dict) and book_data.get("code") == 0 and book_data.get("rendezVousId"): # نجاح الحجز
                        member_obj.rdv_id = book_data.get("rendezVousId")
                        member_obj.rdv_date = formatted_date 
                        new_status = "تم الحجز"
                        detail_text_for_gui = f"تم الحجز في: {formatted_date}, ID: {member_obj.rdv_id}"
                        booking_successful = True
                    else: # فشل الحجز لأسباب أخرى
                        new_status = "فشل الحجز"
                        err_msg = str(book_data.get("message", "خطأ غير معروف من الخادم عند الحجز")) if isinstance(book_data, dict) else str(book_data)
                        # التحقق من وجود نص خام يشير إلى عدم الأهلية
                        if isinstance(book_data, dict) and "raw_text" in book_data and "\"Eligible\":false" in book_data["raw_text"].lower(): 
                             new_status = "غير مؤهل للحجز"
                             detail_text_for_gui = "نعتذر منكم! لا يمكنكم حجز موعد للاستفادة من منحة البطالة لعدم استيفائك لأحد شروط الأهلية اللازمة. (استجابة نصية)"
                             logger.warning(f"العضو {member_obj.nin} غير مؤهل للحجز (استجابة نصية): {book_data['raw_text'][:200]}")
                        else:
                            detail_text_for_gui = f"فشل الحجز: {err_msg}"
                            api_error_occurred_this_stage = True 
                else: # استجابة فارغة عند الحجز
                    new_status = "فشل الحجز"
                    detail_text_for_gui = "استجابة غير متوقعة أو فارغة عند محاولة الحجز."
                    api_error_occurred_this_stage = True
            else: # لا توجد مواعيد متاحة
                new_status = "لا توجد مواعيد"
                detail_text_for_gui = "لا توجد مواعيد متاحة حاليًا."
                # إذا لم يكن لديه تسجيل مسبق فعلي، تبقى الحالة "يتطلب تسجيل مسبق"
                if not member_obj.has_actual_pre_inscription: 
                    new_status = "يتطلب تسجيل مسبق"
                    detail_text_for_gui = "مؤهل ولكن لا يوجد تسجيل مسبق بعد (لا مواعيد متاحة)."
        else: # فشل جلب التواريخ أو استجابة غير صالحة
            new_status = "فشل جلب التواريخ"
            detail_text_for_gui = "لم يتم العثور على تواريخ أو استجابة غير صالحة من الخادم."
            api_error_occurred_this_stage = True
        
        icon = get_icon_name_for_status(new_status)
        self._update_member_and_emit(main_list_idx, member_obj, new_status, detail_text_for_gui, icon)
        return booking_successful, api_error_occurred_this_stage

    def _download_single_pdf_for_monitoring(self, main_list_idx, member_obj, report_type, filename_suffix_base, member_specific_dir):
        # دالة مساعدة لتحميل ملف PDF واحد (للاستخدام داخل خيط المراقبة)
        file_path = None
        success = False
        error_msg_for_toast = ""
        status_msg_for_gui_cell = f"جاري تحميل {report_type.replace('Report','')}..."
        
        current_path_attr = 'pdf_honneur_path' if report_type == "HonneurEngagementReport" else 'pdf_rdv_path'
        
        # التحقق مما إذا كان الملف موجودًا بالفعل
        current_pdf_path_value = getattr(member_obj, current_path_attr)
        if current_pdf_path_value and os.path.exists(current_pdf_path_value):
            logger.info(f"ملف {report_type} موجود بالفعل للعضو {member_obj.nin} في {current_pdf_path_value}. تخطي التحميل.")
            # إرجاع المسار الحالي كنجاح، مع رسالة مناسبة للواجهة
            return current_pdf_path_value, True, "", f"{report_type.replace('Report','')} موجود بالفعل."

        # تحديث واجهة المستخدم بحالة "جاري التحميل"
        self._update_member_and_emit(main_list_idx, member_obj, status_msg_for_gui_cell, f"بدء تحميل {report_type}", get_icon_name_for_status(status_msg_for_gui_cell))
        
        response_data, api_err = self.api_client.download_pdf(report_type, member_obj.pre_inscription_id)

        if api_err:
            error_msg_for_toast = f"خطأ من الخادم عند تحميل {report_type}: {api_err}"
        elif response_data and (isinstance(response_data, str) or (isinstance(response_data, dict) and "base64Pdf" in response_data)):
            pdf_b64 = response_data if isinstance(response_data, str) else response_data.get("base64Pdf")
            try:
                pdf_content = base64.b64decode(pdf_b64)
                # إنشاء اسم ملف آمن
                safe_member_name_part = "".join(c for c in (member_obj.get_full_name_ar() or member_obj.nin) if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ","_")
                if not safe_member_name_part: safe_member_name_part = member_obj.nin # احتياطي
                final_filename = f"{filename_suffix_base}_{safe_member_name_part}.pdf" 
                file_path = os.path.join(member_specific_dir, final_filename)
                with open(file_path, 'wb') as f:
                    f.write(pdf_content)
                setattr(member_obj, current_path_attr, file_path) # حفظ مسار الملف في كائن العضو
                success = True
                status_msg_for_gui_cell = f"تم تحميل {final_filename} بنجاح."
            except Exception as e_save:
                error_msg_for_toast = f"خطأ في حفظ ملف {report_type}: {str(e_save)}"
        else:
            error_msg_for_toast = f"استجابة غير متوقعة من الخادم لـ {report_type}."
        
        if not success:
            status_msg_for_gui_cell = f"فشل تحميل {report_type.replace('Report','')}: {error_msg_for_toast.split(':')[0]}" # عرض الجزء الأول من الخطأ
        
        # لا يتم إرسال إشارة لواجهة المستخدم من هنا مباشرة، بل يتم إرجاع النتائج ليتم تجميعها
        return file_path, success, error_msg_for_toast, status_msg_for_gui_cell

    def process_pdf_download(self, main_list_idx, member_obj): 
        # معالجة تحميل ملفات PDF لعضو معين
        if not member_obj.pre_inscription_id:
            detail_text = "ID التسجيل مفقود لتحميل PDF."
            self._update_member_and_emit(main_list_idx, member_obj, member_obj.status, detail_text, get_icon_name_for_status(member_obj.status))
            return False, False # لا يمكن المتابعة، لا يعتبر خطأ API
        
        # تحديد مسار حفظ الملفات
        documents_location = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base_app_dir_name = "ملفات_المنحة_البرنامج"
        member_name_for_folder = member_obj.get_full_name_ar()
        if not member_name_for_folder or member_name_for_folder.isspace(): 
            member_name_for_folder = member_obj.nin 
        
        safe_folder_name_part = "".join(c for c in member_name_for_folder if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ", "_")
        if not safe_folder_name_part: safe_folder_name_part = member_obj.nin 
        
        member_specific_output_dir = os.path.join(documents_location, base_app_dir_name, safe_folder_name_part)
        
        try:
            os.makedirs(member_specific_output_dir, exist_ok=True) # إنشاء المجلد إذا لم يكن موجودًا
        except Exception as e_mkdir:
            logger.error(f"فشل إنشاء مجلد للعضو {member_obj.nin} في process_pdf_download: {e_mkdir}")
            self._update_member_and_emit(main_list_idx, member_obj, "فشل تحميل PDF", f"فشل إنشاء مجلد: {e_mkdir}", get_icon_name_for_status("فشل تحميل PDF"))
            return False, False # لا يمكن المتابعة، يعتبر خطأ في الإعداد
        
        all_relevant_pdfs_downloaded_successfully = True
        any_api_error_this_pdf_stage = False
        download_details_agg = [] # لتجميع رسائل حالة تحميل كل ملف

        # تحميل ملف التعهد بالالتزام
        if not self.is_running: return False, any_api_error_this_pdf_stage # التحقق من علم الإيقاف
        fp_h, s_h, err_h, stat_h = self._download_single_pdf_for_monitoring(main_list_idx, member_obj, "HonneurEngagementReport", "التزام", member_specific_output_dir)
        download_details_agg.append(stat_h)
        if not s_h: all_relevant_pdfs_downloaded_successfully = False
        if err_h: any_api_error_this_pdf_stage = True # تسجيل حدوث خطأ API
        
        # تحميل ملف الموعد إذا كان العضو لديه موعد
        if self.is_running and (member_obj.already_has_rdv or member_obj.rdv_id): 
            fp_r, s_r, err_r, stat_r = self._download_single_pdf_for_monitoring(main_list_idx, member_obj, "RdvReport", "موعد", member_specific_output_dir)
            download_details_agg.append(stat_r)
            if not s_r: all_relevant_pdfs_downloaded_successfully = False
            if err_r: any_api_error_this_pdf_stage = True
        elif self.is_running: # إذا لم يكن لديه موعد
            msg_skip_rdv = "شهادة الموعد غير مطلوبة."
            logger.info(msg_skip_rdv + f" للعضو {member_obj.nin}")
            download_details_agg.append(msg_skip_rdv)
        
        # تحديد الحالة النهائية بناءً على نجاح التحميلات
        final_status_after_pdfs = member_obj.status
        if all_relevant_pdfs_downloaded_successfully:
            if member_obj.status != "مستفيد حاليًا من المنحة": # لا تغير الحالة إذا كان مستفيدًا بالفعل
                 final_status_after_pdfs = "مكتمل"
        else:
            final_status_after_pdfs = "فشل تحميل PDF" # إذا فشل تحميل أي ملف
            
        final_detail_message = "; ".join(msg for msg in download_details_agg if msg) # تجميع رسائل الحالة
        self._update_member_and_emit(main_list_idx, member_obj, final_status_after_pdfs, final_detail_message, get_icon_name_for_status(final_status_after_pdfs))
        
        return all_relevant_pdfs_downloaded_successfully, any_api_error_this_pdf_stage

    def stop_monitoring(self): 
        # إيقاف خيط المراقبة
        logger.info("طلب إيقاف المراقبة...")
        self.is_running = False


class SingleMemberCheckThread(QThread):
    # خيط لفحص عضو واحد بشكل فوري
    update_member_gui_signal = pyqtSignal(int, str, str, str) 
    new_data_fetched_signal = pyqtSignal(int, str, str)      
    member_processing_started_signal = pyqtSignal(int)       
    member_processing_finished_signal = pyqtSignal(int)      
    global_log_signal = pyqtSignal(str)                      

    def __init__(self, member, index, api_client, settings, parent=None):
        super().__init__(parent)
        self.member = member 
        self.index = index   
        self.api_client = api_client
        self.settings = settings 
        self.is_running = True # علم للتحكم في عمل الخيط

    def stop(self):
        # إيقاف الخيط
        self.is_running = False
        logger.info(f"طلب إيقاف خيط الفحص الفردي للعضو: {self.member.nin}")

    def run(self):
        logger.info(f"بدء فحص فوري للعضو: {self.member.nin} (فهرس: {self.index})")
        self.member_processing_started_signal.emit(self.index) # إرسال إشارة بدء المعالجة
        self.global_log_signal.emit(f"بدء الفحص الفوري للعضو: {self.member.get_full_name_ar() or self.member.nin}")

        member_had_api_error_overall = False # لتتبع أخطاء الشبكة خلال هذا الفحص
        
        # استخدام نسخة مؤقتة من MonitoringThread لتوفير منطق المعالجة
        # هذا لا يستدعي run() الخاص بـ MonitoringThread، بل يستخدم فقط دوال المعالجة الخاصة به
        temp_monitor_logic_provider = MonitoringThread(members_list_ref=[self.member], settings=self.settings) 
        temp_monitor_logic_provider.is_running = self.is_running # مزامنة حالة التشغيل
        # ربط إشارات الخيط المؤقت بإشارات هذا الخيط لإعادة توجيهها
        temp_monitor_logic_provider.update_member_gui_signal.connect(self.update_member_gui_signal) 
        temp_monitor_logic_provider.new_data_fetched_signal.connect(self.new_data_fetched_signal)
        # لا نربط global_log_signal من temp_monitor لأنه قد يكون مربكًا، هذا الخيط له سجلاته الخاصة

        try:
            if not self.is_running: return # التحقق من علم الإيقاف

            # تحديث الحالة الأولية للفحص الفوري
            self.member.status = "جاري التحقق (فوري)..." 
            self.member.set_activity_detail(f"التحقق من صحة بيانات {self.member.nin}")
            self._emit_gui_update() # إرسال تحديث لواجهة المستخدم
            if not self.is_running: return

            # 1. معالجة التحقق من صحة البيانات
            validation_can_progress, api_error_validation = temp_monitor_logic_provider.process_validation(0, self.member) # الفهرس دائمًا 0 لأنه عضو واحد
            if api_error_validation: member_had_api_error_overall = True
            self._emit_gui_update() 
            if not self.is_running: return

            # إذا كانت الحالة نهائية بعد التحقق، لا تتابع
            if self.member.status in ["مستفيد حاليًا من المنحة", "بيانات الإدخال خاطئة", "فشل التحقق", "غير مؤهل مبدئيًا", "لديه موعد مسبق", "غير مؤهل للحجز"]:
                logger.info(f"الفحص الفوري: الحالة النهائية بعد التحقق أو حالة تمنع المتابعة: {self.member.status}")
                return # الخروج من الدالة run

            # 2. جلب معلومات التسجيل المسبق (الاسم) إذا لزم الأمر
            if validation_can_progress and self.member.pre_inscription_id and not (self.member.nom_ar and self.member.prenom_ar):
                if not self.is_running: return
                info_success, api_error_info = temp_monitor_logic_provider.process_pre_inscription_info(0, self.member)
                if api_error_info: member_had_api_error_overall = True
                self._emit_gui_update()
                if not self.is_running: return
                if self.member.status == "فشل جلب المعلومات": 
                    logger.info(f"الفحص الفوري: فشل جلب الاسم.")
                    return
            
            # 3. محاولة حجز موعد إذا كانت الشروط مناسبة
            if self.member.status in ["تم جلب المعلومات", "تم التحقق", "لا توجد مواعيد", "فشل جلب التواريخ", "يتطلب تسجيل مسبق"] and \
               self.member.has_actual_pre_inscription and self.member.pre_inscription_id and \
               self.member.demandeur_id and self.member.structure_id and \
               not self.member.already_has_rdv and not self.member.have_allocation: 
                if not self.is_running: return
                booking_successful, api_error_booking = temp_monitor_logic_provider.process_available_dates_and_book(0, self.member)
                if api_error_booking: member_had_api_error_overall = True
                self._emit_gui_update()
                if not self.is_running: return
                if self.member.status in ["فشل الحجز", "غير مؤهل للحجز"]:
                    logger.info(f"الفحص الفوري: فشل الحجز أو غير مؤهل.")
                    return
            
            # 4. محاولة تحميل ملفات PDF إذا كانت الحالة تسمح بذلك
            pdf_attempt_worthy_statuses_for_single_check = ["تم الحجز", "لديه موعد مسبق", "مستفيد حاليًا من المنحة", "مكتمل", "فشل تحميل PDF"] # إضافة فشل تحميل PDF
            if self.member.status in pdf_attempt_worthy_statuses_for_single_check and self.member.pre_inscription_id:
                if not self.is_running: return
                logger.info(f"الفحص الفوري للعضو {self.member.nin} ({self.member.status}) يستدعي محاولة تحميل PDF.")
                pdf_success, api_error_pdf = temp_monitor_logic_provider.process_pdf_download(0, self.member)
                if api_error_pdf: member_had_api_error_overall = True
                self._emit_gui_update()
                if not self.is_running: return
            
            # تحديث الحالة النهائية بعد الفحص الفوري
            final_log_message = f"الفحص الفوري للعضو {self.member.nin} انتهى بالحالة: {self.member.status}. التفاصيل: {self.member.full_last_activity_detail}"
            logger.info(final_log_message)
            self.global_log_signal.emit(f"فحص {self.member.nin}: {self.member.status} - {self.member.last_activity_detail}")

        except Exception as e:
            logger.exception(f"خطأ غير متوقع في SingleMemberCheckThread للعضو {self.member.nin}: {e}")
            self.member.status = "خطأ في الفحص الفوري"
            self.member.set_activity_detail(f"خطأ عام: {str(e)}", is_error=True)
            self.global_log_signal.emit(f"خطأ فحص {self.member.nin}: {e}")
        finally:
            temp_monitor_logic_provider.is_running = False # التأكد من إيقاف الخيط المؤقت إذا لزم الأمر
            self._emit_gui_update() # التأكد من تحديث واجهة المستخدم بالحالة النهائية
            self.member_processing_finished_signal.emit(self.index) # إرسال إشارة انتهاء المعالجة
            logger.info(f"انتهاء الفحص الفوري للعضو: {self.member.nin}")

    def _emit_gui_update(self):
        # دالة مساعدة لإرسال تحديث لواجهة المستخدم
        if not self.is_running: return # عدم الإرسال إذا تم إيقاف الخيط
        final_icon = get_icon_name_for_status(self.member.status)
        self.update_member_gui_signal.emit(self.index, self.member.status, self.member.last_activity_detail, final_icon)


class DownloadAllPdfsThread(QThread): 
    # خيط لتحميل جميع ملفات PDF لعضو معين
    all_pdfs_download_finished_signal = pyqtSignal(int, str, str, str, bool, str) # (index, honneur_path, rdv_path, overall_status, all_success, first_error)
    individual_pdf_status_signal = pyqtSignal(int, str, str, bool, str) # (index, pdf_type, path_or_msg, success, error_toast_msg)
    member_processing_started_signal = pyqtSignal(int)
    member_processing_finished_signal = pyqtSignal(int)
    global_log_signal = pyqtSignal(str)

    def __init__(self, member, index, api_client, parent=None):
        super().__init__(parent)
        self.member = member
        self.index = index
        self.api_client = api_client
        self.is_running = True # علم للتحكم في عمل الخيط

    def _download_single_pdf(self, pdf_type, filename_suffix_base, member_specific_dir):
        # دالة مساعدة لتحميل ملف PDF واحد (للاستخدام داخل هذا الخيط)
        file_path = None
        success = False
        error_msg_toast = "" 
        status_for_gui_cell = f"جاري تحميل {pdf_type.replace('Report','')}..."
        self.global_log_signal.emit(f"{status_for_gui_cell} لـ {self.member.get_full_name_ar() or self.member.nin}")

        if not self.member.pre_inscription_id:
            error_msg_toast = "ID التسجيل المسبق مفقود."
            status_for_gui_cell = f"فشل: {error_msg_toast}"
            # إرسال إشارة بحالة تحميل هذا الملف الفردي
            self.individual_pdf_status_signal.emit(self.index, pdf_type, status_for_gui_cell, False, error_msg_toast)
            return None, False, error_msg_toast, status_for_gui_cell

        current_path_attr = 'pdf_honneur_path' if pdf_type == "HonneurEngagementReport" else 'pdf_rdv_path'
        
        # التحقق مما إذا كان الملف موجودًا بالفعل
        current_pdf_path_value = getattr(self.member, current_path_attr)
        if current_pdf_path_value and os.path.exists(current_pdf_path_value):
            logger.info(f"ملف {pdf_type} موجود بالفعل للعضو {self.member.nin} في {current_pdf_path_value}. تخطي التحميل.")
            status_for_gui_cell = f"{pdf_type.replace('Report','')} موجود بالفعل."
            self.individual_pdf_status_signal.emit(self.index, pdf_type, current_pdf_path_value, True, "") # إرسال نجاح مع المسار
            return current_pdf_path_value, True, "", status_for_gui_cell

        # استدعاء الواجهة البرمجية لتحميل الملف
        response_data, api_err = self.api_client.download_pdf(pdf_type, self.member.pre_inscription_id)

        if api_err:
            error_msg_toast = f"خطأ من الخادم عند تحميل {pdf_type}: {api_err}"
        elif response_data and (isinstance(response_data, str) or (isinstance(response_data, dict) and "base64Pdf" in response_data)):
            pdf_b64 = response_data if isinstance(response_data, str) else response_data.get("base64Pdf")
            try:
                pdf_content = base64.b64decode(pdf_b64)
                # إنشاء اسم ملف آمن
                safe_member_name_part = "".join(c for c in (self.member.get_full_name_ar() or self.member.nin) if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ","_")
                if not safe_member_name_part: safe_member_name_part = self.member.nin 
                filename = f"{filename_suffix_base}_{safe_member_name_part}.pdf" 
                file_path = os.path.join(member_specific_dir, filename)
                with open(file_path, 'wb') as f:
                    f.write(pdf_content)
                setattr(self.member, current_path_attr, file_path) # حفظ مسار الملف
                success = True
                status_for_gui_cell = f"تم تحميل {filename} بنجاح."
            except Exception as e_save:
                error_msg_toast = f"خطأ في حفظ ملف {pdf_type}: {str(e_save)}"
        else:
            error_msg_toast = f"استجابة غير متوقعة من الخادم لـ {pdf_type}."
        
        if not success:
            status_for_gui_cell = f"فشل تحميل {pdf_type.replace('Report','')}: {error_msg_for_toast.split(':')[0]}"
        
        # إرسال إشارة بحالة تحميل هذا الملف الفردي
        self.individual_pdf_status_signal.emit(self.index, pdf_type, file_path if success else status_for_gui_cell, success, error_msg_toast)
        return file_path, success, error_msg_toast, status_for_gui_cell

    def run(self):
        logger.info(f"بدء تحميل جميع الشهادات للعضو: {self.member.nin}")
        self.member_processing_started_signal.emit(self.index) # إرسال إشارة بدء المعالجة
        self.global_log_signal.emit(f"جاري تحميل شهادات لـ {self.member.get_full_name_ar() or self.member.nin}...")

        all_downloads_successful = True # علم لتتبع نجاح تحميل جميع الملفات المطلوبة
        first_error_encountered = "" # لتخزين أول خطأ يحدث
        aggregated_status_messages = [] # لتجميع رسائل حالة كل ملف
        
        path_honneur_final = self.member.pdf_honneur_path # المسار النهائي لملف التعهد
        path_rdv_final = self.member.pdf_rdv_path       # المسار النهائي لملف الموعد

        # تحديد مسار حفظ الملفات
        documents_location = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        base_app_dir_name = "ملفات_المنحة_البرنامج"
        member_name_for_folder = self.member.get_full_name_ar()
        if not member_name_for_folder or member_name_for_folder.isspace(): 
            member_name_for_folder = self.member.nin 
        
        safe_folder_name_part = "".join(c for c in member_name_for_folder if c.isalnum() or c in (' ', '_', '-')).rstrip().replace(" ", "_")
        if not safe_folder_name_part: safe_folder_name_part = self.member.nin 
        
        member_specific_output_dir = os.path.join(documents_location, base_app_dir_name, safe_folder_name_part)
        
        try:
            os.makedirs(member_specific_output_dir, exist_ok=True) # إنشاء المجلد إذا لم يكن موجودًا
            logger.info(f"تم إنشاء/التحقق من مجلد العضو: {member_specific_output_dir}")
        except Exception as e_mkdir:
            logger.error(f"فشل إنشاء مجلد للعضو {self.member.nin}: {e_mkdir}")
            self.all_pdfs_download_finished_signal.emit(self.index, None, None, f"فشل إنشاء مجلد: {e_mkdir}", False, str(e_mkdir))
            self.member_processing_finished_signal.emit(self.index)
            return

        # تحميل ملف التعهد بالالتزام
        if not self.is_running: self.member_processing_finished_signal.emit(self.index); return # التحقق من علم الإيقاف
        fp_h, s_h, err_h, stat_h = self._download_single_pdf("HonneurEngagementReport", "التزام", member_specific_output_dir)
        aggregated_status_messages.append(stat_h)
        if s_h: path_honneur_final = fp_h
        else: all_downloads_successful = False; first_error_encountered = first_error_encountered or err_h # تسجيل أول خطأ
        
        # تحميل ملف الموعد إذا كان العضو لديه موعد
        if self.is_running and (self.member.already_has_rdv or self.member.rdv_id): 
            fp_r, s_r, err_r, stat_r = self._download_single_pdf("RdvReport", "موعد", member_specific_output_dir)
            aggregated_status_messages.append(stat_r)
            if s_r: path_rdv_final = fp_r
            else: all_downloads_successful = False; first_error_encountered = first_error_encountered or err_r
        elif self.is_running: # إذا لم يكن لديه موعد، لا حاجة لتحميل ملف الموعد
            msg_skip_rdv = "شهادة الموعد غير مطلوبة/متوفرة (لا يوجد موعد مسجل)."
            logger.info(msg_skip_rdv + f" للعضو {self.member.nin}")
            aggregated_status_messages.append(msg_skip_rdv)
            # إرسال إشارة بأن هذا الملف تم "تخطيه بنجاح" لأنه غير مطلوب
            self.individual_pdf_status_signal.emit(self.index, "RdvReport", msg_skip_rdv, True, "") 

        # تحديد رسالة الحالة الإجمالية
        final_overall_status_msg_for_signal = "; ".join(msg for msg in aggregated_status_messages if msg)
        if not all_downloads_successful and first_error_encountered:
            final_overall_status_msg_for_signal = f"فشل تحميل بعض الملفات. أول خطأ: {first_error_encountered.split(':')[0]}"
        elif all_downloads_successful:
             final_overall_status_msg_for_signal = "تم تحميل جميع الشهادات المطلوبة بنجاح."
        
        # إرسال إشارة انتهاء تحميل جميع الملفات
        self.all_pdfs_download_finished_signal.emit(self.index, path_honneur_final, path_rdv_final, final_overall_status_msg_for_signal, all_downloads_successful, first_error_encountered)
        self.member_processing_finished_signal.emit(self.index) # إرسال إشارة انتهاء المعالجة
        logger.info(f"انتهاء تحميل جميع الشهادات للعضو: {self.member.nin}. النجاح الكلي: {all_downloads_successful}")

    def stop(self): 
        # إيقاف الخيط
        self.is_running = False
        logger.info(f"طلب إيقاف خيط تحميل جميع الشهادات للعضو: {self.member.nin}")
