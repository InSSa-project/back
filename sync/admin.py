from django.contrib import admin

from .models import CrawlJobLog, RawSsafyData


@admin.register(RawSsafyData)
class RawSsafyDataAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'source_type', 'status', 'collected_at')
    list_filter = ('source_type', 'status')
    search_fields = ('title', 'raw_text', 'source_url')


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
    )
    list_filter = ('status', 'crawler_mode')

