from django.contrib import admin

from .models import Match, Participation, Pitch


class ParticipationInline(admin.TabularInline):
    model = Participation
    extra = 0
    readonly_fields = ("joined_at",)


@admin.register(Pitch)
class PitchAdmin(admin.ModelAdmin):
    list_display = ("name", "sector", "address", "created_by")
    list_filter = ("sector",)
    search_fields = ("name", "address")


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("pitch", "start_time", "organizer", "spots_needed", "spots_left", "status")
    list_filter = ("status", "pitch__sector")
    search_fields = ("pitch__name", "organizer__username")
    date_hierarchy = "start_time"
    inlines = [ParticipationInline]


@admin.register(Participation)
class ParticipationAdmin(admin.ModelAdmin):
    list_display = ("player", "match", "joined_at")
    search_fields = ("player__username", "match__pitch__name")