# utils.py
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication, QStyle 

class QColorConstants: # Dark Theme Specific Colors
    PINK_DARK_THEME = QColor(176, 56, 73)
    LIGHT_PINK_DARK_THEME = QColor(130, 70, 80) 
    LIGHT_GREEN_DARK_THEME = QColor(60, 140, 70) 
    LIGHT_YELLOW_DARK_THEME = QColor(180, 160, 80) 
    LIGHT_BLUE_DARK_THEME = QColor(70, 100, 150) 
    ORANGE_RED_DARK_THEME = QColor(190, 70, 50) 
    PROCESSING_ROW_DARK_THEME = QColor(80, 80, 110) 
    BENEFITING_GREEN_DARK_THEME = QColor(30, 100, 50) # New color for "مستفيد حاليًا"

def get_icon_name_for_status(status_text):
    """
    Determines the QStyle standard pixmap name string based on member status.
    Returns a string like "SP_DialogYesButton".
    """
    # Order matters: more specific checks should come before general ones.
    
    if status_text == "مستفيد حاليًا من المنحة": return "SP_ FEATURE_खुशी" # Using a "happy" or "star" like icon if available, SP_DialogApplyButton as fallback
    if status_text == "مكتمل": return "SP_DialogYesButton"
    if status_text == "تم الحجز": return "SP_DialogSaveButton" 
    if status_text == "تم جلب المعلومات": return "SP_DialogApplyButton" 
    if status_text == "تم التحقق": return "SP_DialogApplyButton"
    if status_text == "تم التحقق (فوري)": return "SP_DialogApplyButton"
    if status_text == "تم جلب المعلومات (فوري)": return "SP_DialogApplyButton"


    if "فشل" in status_text or "خاطئة" in status_text or "خطأ" in status_text: return "SP_MessageBoxCritical"
    if "غير مؤهل" in status_text : return "SP_MessageBoxCritical" 
    
    if "لديه موعد مسبق" in status_text: return "SP_MessageBoxInformation" 
    if "يتطلب تسجيل مسبق" in status_text: return "SP_MessageBoxWarning"
    if "لا توجد مواعيد" in status_text: return "SP_MessageBoxInformation"
    if status_text == "فشل بشكل متكرر": return "SP_MessageBoxWarning" 

    if "جاري" in status_text or "البحث" in status_text or "محاولة" in status_text : return "SP_ArrowRight" 
    
    if status_text == "جديد": return "SP_CustomBase" 
    
    return "SP_CustomBase" 
