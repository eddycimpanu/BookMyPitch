from django.contrib import admin

from .models import (
    BlockedTime,
    Booking,
    ChatMessage,
    Match,
    MatchExit,
    OpeningHours,
    Participation,
    Pitch,
    WaitlistEntry,
)


class ParticipationInline(admin.TabularInline):
    model = Participation
    extra = 0
    readonly_fields = ("joined_at",)


class WaitlistInline(admin.TabularInline):
    model = WaitlistEntry
    extra = 0
    readonly_fields = ("joined_at",)


class OpeningHoursInline(admin.TabularInline):
    model = OpeningHours
    extra = 0


@admin.register(Pitch)
class PitchAdmin(admin.ModelAdmin):
    list_display = ("name", "sector", "address", "owner", "price_per_hour", "created_by")
    list_filter = ("sector",)
    search_fields = ("name", "address")
    inlines = [OpeningHoursInline]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ("pitch", "start_time", "organizer", "spots_needed", "spots_left", "status")
    list_filter = ("status", "pitch__sector")
    search_fields = ("pitch__name", "organizer__username")
    date_hierarchy = "start_time"
    inlines = [ParticipationInline, WaitlistInline]


@admin.register(Participation)
class ParticipationAdmin(admin.ModelAdmin):
    list_display = ("player", "match", "joined_at")
    search_fields = ("player__username", "match__pitch__name")


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ("player", "match", "joined_at")
    search_fields = ("player__username", "match__pitch__name")


@admin.register(MatchExit)
class MatchExitAdmin(admin.ModelAdmin):
    list_display = ("player", "match", "reason", "created_at")
    list_filter = ("reason",)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("author", "match", "body", "created_at")
    search_fields = ("body", "author__username")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("pitch", "start_time", "end_time", "price", "status", "cancelled_by", "match")
    list_filter = ("status",)
    date_hierarchy = "start_time"


@admin.register(BlockedTime)
class BlockedTimeAdmin(admin.ModelAdmin):
    list_display = ("pitch", "start_time", "end_time", "reason")
    date_hierarchy = "start_time"
