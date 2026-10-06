from django.contrib import admin
from .models import EventGameConfiguration, GameDefinition, GameSession

@admin.register(GameDefinition)
class GameDefinitionAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'key', 'implementation_key', 'active', 'implementation_version')
    list_filter = ('active',)
    search_fields = ('key', 'display_name')

@admin.register(EventGameConfiguration)
class EventGameConfigurationAdmin(admin.ModelAdmin):
    list_display = ('event', 'game', 'configuration_version', 'updated_at')
    list_filter = ('game',)
    search_fields = ('event__name', 'game__display_name')

@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ('game', 'event', 'mode', 'status', 'raw_score', 'started_at', 'completed_at')
    list_filter = ('game', 'mode', 'status')
    readonly_fields = ('id', 'created_at', 'started_at', 'completed_at', 'voided_at', 'configuration_snapshot', 'result_data')
