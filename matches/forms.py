from decimal import Decimal

from django import forms
from django.utils import timezone

from accounts.forms import BootstrapFormMixin

from .models import Match, Pitch

# Same area as the validators on the Pitch model: Bucharest + Ilfov
LATITUDE_RANGE = (Decimal("44.2"), Decimal("44.7"))
LONGITUDE_RANGE = (Decimal("25.8"), Decimal("26.4"))
LOCATION_REQUIRED = "Click on the map to choose the pitch location."
LOCATION_OUT_OF_AREA = "Choose a location in Bucharest or Ilfov."


class PitchForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Pitch
        fields = ["name", "address", "sector", "latitude", "longitude"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Arena Tineretului"}),
            "address": forms.TextInput(attrs={"placeholder": "Street and number"}),
            "latitude": forms.HiddenInput,
            "longitude": forms.HiddenInput,
        }
        error_messages = {
            "latitude": {"required": LOCATION_REQUIRED},
            "longitude": {"required": LOCATION_REQUIRED},
        }

    def _clean_coordinate(self, name, allowed_range):
        value = self.cleaned_data[name]
        low, high = allowed_range
        if not low <= value <= high:
            raise forms.ValidationError(LOCATION_OUT_OF_AREA)
        return value

    def clean_latitude(self):
        return self._clean_coordinate("latitude", LATITUDE_RANGE)

    def clean_longitude(self):
        return self._clean_coordinate("longitude", LONGITUDE_RANGE)


class MatchForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Match
        fields = ["pitch", "start_time", "duration_minutes", "spots_needed", "price_per_player", "description"]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "description": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Level, what to bring, where to meet..."}
            ),
        }
        labels = {
            "start_time": "Kick-off",
            "duration_minutes": "Duration",
            "spots_needed": "Players needed",
            "price_per_player": "Price per player (RON)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pitch"].queryset = Pitch.objects.order_by("name")
        self.fields["pitch"].empty_label = "Choose a pitch"

    def clean_start_time(self):
        start_time = self.cleaned_data["start_time"]
        if start_time <= timezone.now():
            raise forms.ValidationError("Kick-off must be in the future.")
        return start_time

    def clean_spots_needed(self):
        spots_needed = self.cleaned_data["spots_needed"]
        if self.instance.pk:
            taken = self.instance.participations.count()
            if spots_needed < taken:
                raise forms.ValidationError(
                    f"{taken} players already joined. Keep at least {taken} spots or remove players first."
                )
        return spots_needed