from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


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