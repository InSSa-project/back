from django.contrib import admin

from .models import CrawlJobLog, RawSsafyData


@admin.register(RawSsafyData)
class RawSsafyDataAdmin(admin.ModelAdmin):
    list_display = ('id', 'source_type', 'title', 'source_url', 'status', 'collected_at', 'raw_text_preview')
    list_filter = ('source_type', 'status')
    search_fields = ('title', 'raw_text', 'source_url')
    readonly_fields = ('raw_text_preview',)

    def raw_text_preview(self, obj):
        return obj.raw_text[:160]

    raw_text_preview.short_description = 'raw_text preview'


@admin.register(CrawlJobLog)
class CrawlJobLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'status',
        'crawler_mode',
        'started_at',
        'finished_at',
        'raw_count',
        'event_count',
        'failed_count',
        'skipped_count',
        'notice_count',
        'academic_rule_count',
        'no_schedule_count',
        'image_count',
        'ocr_processed_count',
        'ocr_failed_count',
        'message_preview',
    )
    list_filter = ('status', 'crawler_mode')
    search_fields = ('message',)

    def message_preview(self, obj):
        return obj.message[:180]

    message_preview.short_description = 'message preview'

