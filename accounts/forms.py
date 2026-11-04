from io import BytesIO

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps

from .models import User

# Profile photos are stored as small square JPEGs, whatever the player uploads
MAX_PHOTO_MB = 3
PHOTO_SIZE = 256


class BootstrapFormMixin:
    """Adds Bootstrap CSS classes to every field widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.RadioSelect):
                widget.attrs["class"] = "form-check-input me-1"
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            elif not isinstance(widget, forms.HiddenInput):
                widget.attrs["class"] = "form-control"

class SignUpForm(BootstrapFormMixin, UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "role")
        widgets = {"role": forms.RadioSelect}
        labels = {"role": "I want to join as"}


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    pass


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    remove_photo = forms.BooleanField(required=False, label="Remove my photo")

    class Meta:
        model = User
        fields = ("position", "level", "photo")
        widgets = {"photo": forms.FileInput(attrs={"accept": "image/*"})}
        labels = {"position": "Favourite position", "photo": "Profile photo"}
        help_texts = {"photo": f"JPG or PNG, up to {MAX_PHOTO_MB} MB. It is cropped to a square."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["remove_photo"].widget.attrs["class"] = "form-check-input"
        for name in ("position", "level"):
            self.fields[name].choices = [("", "Not set"), *self.fields[name].choices[1:]]
        if self.instance.is_owner:
            # Position and level are about playing, so pitch owners only get a photo
            del self.fields["position"]
            del self.fields["level"]
        if not self.instance.photo:
            del self.fields["remove_photo"]

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        # A new upload is an UploadedFile. Otherwise it's the photo already saved (or nothing).
        if isinstance(photo, UploadedFile) and photo.size > MAX_PHOTO_MB * 1024 * 1024:
            raise forms.ValidationError(f"The photo is too big. Use one up to {MAX_PHOTO_MB} MB.")
        return photo

    def save(self, commit=True):
        user = super().save(commit=False)
        old_photo = self.initial.get("photo")
        new_photo = self.cleaned_data.get("photo")

        if self.cleaned_data.get("remove_photo"):
            user.photo = ""
        elif isinstance(new_photo, UploadedFile):
            user.photo.save(f"{user.username}.jpg", square_jpeg(new_photo), save=False)

        if commit:
            user.save()
            if old_photo and old_photo.name != user.photo.name:
                old_photo.storage.delete(old_photo.name)  # don't keep photos nobody uses
        return user


def square_jpeg(upload):
    """Crop the uploaded image to a centered square and shrink it to PHOTO_SIZE pixels."""
    image = Image.open(upload)
    image = ImageOps.exif_transpose(image)  # phone photos store their rotation separately
    image = ImageOps.fit(image.convert("RGB"), (PHOTO_SIZE, PHOTO_SIZE))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return ContentFile(buffer.getvalue())
