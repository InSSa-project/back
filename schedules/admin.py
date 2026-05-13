from django.contrib import admin

from .models import ScheduleEvent


@admin.register(ScheduleEvent)
class ScheduleEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'start_at', 'end_at', 'event_type', 'source_type')
    list_filter = ('event_type', 'source_type', 'is_all_day')
    search_fields = ('title', 'description', 'source_id')

