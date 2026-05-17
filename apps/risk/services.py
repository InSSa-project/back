from .models import RiskStatus


class RiskService:
    def get_status(self, user):
        status, _ = RiskStatus.objects.get_or_create(user=user)
        return status
