class NoticeImportService:
    def create_import_log(self, user, import_type):
        from .models import SsafyDataImportLog

        return SsafyDataImportLog.objects.create(user=user, import_type=import_type)
