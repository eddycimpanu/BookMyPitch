from decimal import Decimal

from django import forms
from django.utils import timezone

from accounts.forms import BootstrapFormMixin

from .models import HALF_HOURS, Match, OpeningHours, Pitch

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
        # Pitches listed by an owner are booked through their pitch page, not picked here
        self.fields["pitch"].queryset = Pitch.objects.filter(owner__isnull=True).order_by("name")
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


class OwnerPitchForm(PitchForm):
    """The pitch form for owners: the same details plus a price per hour."""

    class Meta(PitchForm.Meta):
        fields = PitchForm.Meta.fields + ["price_per_hour"]
        labels = {"price_per_hour": "Price per hour (RON)"}
        help_texts = {"price_per_hour": ""}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price_per_hour"].required = True


# Hours suggested when an owner lists a new pitch
DEFAULT_HOURS = {
    **{weekday: (17 * 60, 23 * 60) for weekday in range(5)},  # Monday to Friday, 17:00-23:00
    5: (9 * 60, 23 * 60),  # Saturday, 09:00-23:00
    6: (9 * 60, 23 * 60),  # Sunday, 09:00-23:00
}


class WeeklyHoursForm(forms.Form):
    """One row per day of the week: open or closed, and from when until when."""

    def __init__(self, *args, pitch=None, **kwargs):
        super().__init__(*args, **kwargs)
        saved = {hours.weekday: hours for hours in pitch.opening_hours.all()} if pitch else {}

        self.days = []
        for weekday, name in OpeningHours.Weekday.choices:
            hours = saved.get(weekday)
            opens, closes = (hours.opens_at, hours.closes_at) if hours else DEFAULT_HOURS[weekday]
            is_open = hours is not None if pitch else True

            self.fields[f"day{weekday}_open"] = forms.BooleanField(
                required=False,
                initial=is_open,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
            self.fields[f"day{weekday}_from"] = forms.TypedChoiceField(
                choices=HALF_HOURS[:-1],
                coerce=int,
                initial=opens,
                widget=forms.Select(attrs={"class": "form-select", "aria-label": f"{name} opens at"}),
            )
            self.fields[f"day{weekday}_to"] = forms.TypedChoiceField(
                choices=HALF_HOURS[1:],
                coerce=int,
                initial=closes,
                widget=forms.Select(attrs={"class": "form-select", "aria-label": f"{name} closes at"}),
            )
            self.days.append({
                "name": name,
                "open": self[f"day{weekday}_open"],
                "opens": self[f"day{weekday}_from"],
                "closes": self[f"day{weekday}_to"],
            })

    def clean(self):
        cleaned = super().clean()
        open_days = 0
        for weekday, name in OpeningHours.Weekday.choices:
            if not cleaned.get(f"day{weekday}_open"):
                continue
            open_days += 1
            opens = cleaned.get(f"day{weekday}_from")
            closes = cleaned.get(f"day{weekday}_to")
            if opens is not None and closes is not None and closes - opens < 60:
                self.add_error(f"day{weekday}_to", f"On {name}, close at least 1 hour after opening.")
        if open_days == 0:
            raise forms.ValidationError("Open the pitch on at least one day.")
        return cleaned

    def save(self, pitch):
        """Replace the pitch's opening hours with the ones in the form."""
        pitch.opening_hours.all().delete()
        OpeningHours.objects.bulk_create(
            OpeningHours(
                pitch=pitch,
                weekday=weekday,
                opens_at=self.cleaned_data[f"day{weekday}_from"],
                closes_at=self.cleaned_data[f"day{weekday}_to"],
            )
            for weekday, _ in OpeningHours.Weekday.choices
            if self.cleaned_data[f"day{weekday}_open"]
        )
