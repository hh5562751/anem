# member.py
from config import MAX_ERROR_DISPLAY_LENGTH

class Member:
    def __init__(self, nin, wassit_no, ccp, phone_number=""):
        self.nin = nin
        self.wassit_no = wassit_no
        self.ccp = ccp
        self.phone_number = phone_number
        self.nom_fr = ""
        self.prenom_fr = ""
        self.nom_ar = ""
        self.prenom_ar = ""
        self.pre_inscription_id = None
        self.demandeur_id = None
        self.structure_id = None
        self.status = "جديد"  # Default status for a new member
        self.last_activity_detail = "" # نسخة مختصرة للعرض في الجدول
        self.full_last_activity_detail = "" # نسخة كاملة للتلميح أو السجلات
        self.rdv_date = None
        self.rdv_id = None # To store the RendezVous ID if one exists or is created
        self.pdf_honneur_path = None
        self.pdf_rdv_path = None
        self.is_processing = False # Flag to indicate if member is currently being processed by a thread
        self.has_actual_pre_inscription = False # From validateCandidate API
        self.already_has_rdv = False # From validateCandidate API
        self.consecutive_failures = 0 # Counter for consecutive API call failures for this member
        
        # New attributes for allocation status
        self.have_allocation = False # Indicates if the member is currently benefiting from the allocation
        self.allocation_details = {} # To store the 'detailsAllocation' object or relevant parts


    def get_full_name_ar(self):
        return f"{self.nom_ar or ''} {self.prenom_ar or ''}".strip()

    def to_dict(self):
        # تحويل بيانات العضو إلى قاموس للحفظ
        return {
            'nin': self.nin,
            'wassit_no': self.wassit_no,
            'ccp': self.ccp,
            'phone_number': self.phone_number,
            'nom_fr': self.nom_fr,
            'prenom_fr': self.prenom_fr,
            'nom_ar': self.nom_ar,
            'prenom_ar': self.prenom_ar,
            'pre_inscription_id': self.pre_inscription_id,
            'demandeur_id': self.demandeur_id,
            'structure_id': self.structure_id,
            'status': self.status,
            'last_activity_detail': self.last_activity_detail,
            'full_last_activity_detail': self.full_last_activity_detail,
            'rdv_date': self.rdv_date,
            'rdv_id': self.rdv_id,
            'pdf_honneur_path': self.pdf_honneur_path,
            'pdf_rdv_path': self.pdf_rdv_path,
            'has_actual_pre_inscription': self.has_actual_pre_inscription,
            'already_has_rdv': self.already_has_rdv,
            'consecutive_failures': self.consecutive_failures,
            'have_allocation': self.have_allocation, 
            'allocation_details': self.allocation_details 
        }

    @classmethod
    def from_dict(cls, data):
        # إنشاء كائن عضو من قاموس (عند تحميل البيانات)
        member = cls(data['nin'], data['wassit_no'], data['ccp'], data.get('phone_number', ""))
        member.nom_fr = data.get('nom_fr', "")
        member.prenom_fr = data.get('prenom_fr', "")
        member.nom_ar = data.get('nom_ar', "")
        member.prenom_ar = data.get('prenom_ar', "")
        member.pre_inscription_id = data.get('pre_inscription_id')
        member.demandeur_id = data.get('demandeur_id')
        member.structure_id = data.get('structure_id')
        member.status = data.get('status', "جديد")
        # التأكد من تحميل كلا النوعين من تفاصيل النشاط
        member.full_last_activity_detail = data.get('full_last_activity_detail', data.get('last_activity_detail', "")) # القيمة الافتراضية للكامل هي القديم المختصر إذا لم يوجد الكامل
        member.last_activity_detail = data.get('last_activity_detail', "")
        # إذا كان المختصر فارغًا ولكن الكامل موجود، قم بإنشاء مختصر منه
        if not member.last_activity_detail and member.full_last_activity_detail:
            if len(member.full_last_activity_detail) > MAX_ERROR_DISPLAY_LENGTH:
                member.last_activity_detail = member.full_last_activity_detail[:MAX_ERROR_DISPLAY_LENGTH] + "..."
            else:
                member.last_activity_detail = member.full_last_activity_detail
        
        member.rdv_date = data.get('rdv_date')
        member.rdv_id = data.get('rdv_id')
        member.pdf_honneur_path = data.get('pdf_honneur_path')
        member.pdf_rdv_path = data.get('pdf_rdv_path')
        member.has_actual_pre_inscription = data.get('has_actual_pre_inscription', False)
        member.already_has_rdv = data.get('already_has_rdv', False)
        member.consecutive_failures = data.get('consecutive_failures', 0)
        member.is_processing = False # التأكد من أن حالة المعالجة معطلة عند التحميل
        member.have_allocation = data.get('have_allocation', False)
        member.allocation_details = data.get('allocation_details', {})
        return member

    def set_activity_detail(self, detail_message, is_error=False):
        """
        Sets both full and potentially truncated activity details.
        The `last_activity_detail` will be a summary or truncated version for table display.
        The `full_last_activity_detail` will store the complete message.
        """
        self.full_last_activity_detail = str(detail_message) # التأكد من أنه نص

        # إنشاء نسخة مختصرة لـ last_activity_detail
        # إذا كانت رسالة خطأ، أو رسالة طويلة جدًا، اقتطعها
        # يمكن تحسين هذا المنطق لإنشاء ملخصات أفضل بدلاً من مجرد الاقتطاع
        if is_error or len(self.full_last_activity_detail) > MAX_ERROR_DISPLAY_LENGTH * 1.5: # استخدام مضاعف لغير الأخطاء للسماح بمزيد من التفاصيل
            # محاولة أخذ الجزء الأول ذو المعنى (مثلاً قبل أول نقطة أو فاصلة منقوطة إذا كان خطأ)
            if is_error:
                first_sentence_end = self.full_last_activity_detail.find('.')
                first_line_end = self.full_last_activity_detail.find('\n')
                
                end_index = -1
                if first_sentence_end != -1 and first_line_end != -1:
                    end_index = min(first_sentence_end, first_line_end)
                elif first_sentence_end != -1:
                    end_index = first_sentence_end
                elif first_line_end != -1:
                    end_index = first_line_end

                if end_index != -1 and end_index + 1 <= MAX_ERROR_DISPLAY_LENGTH:
                    self.last_activity_detail = self.full_last_activity_detail[:end_index+1].strip()
                elif len(self.full_last_activity_detail) > MAX_ERROR_DISPLAY_LENGTH:
                    self.last_activity_detail = self.full_last_activity_detail[:MAX_ERROR_DISPLAY_LENGTH] + "..."
                else:
                    self.last_activity_detail = self.full_last_activity_detail
            else: # لغير الأخطاء، يمكن أن يكون الملخص أطول قليلاً
                if len(self.full_last_activity_detail) > MAX_ERROR_DISPLAY_LENGTH:
                     self.last_activity_detail = self.full_last_activity_detail[:MAX_ERROR_DISPLAY_LENGTH] + "..." # استخدام MAX_ERROR_DISPLAY_LENGTH كحد أقصى للملخص أيضًا
                else:
                     self.last_activity_detail = self.full_last_activity_detail
        else:
            self.last_activity_detail = self.full_last_activity_detail

