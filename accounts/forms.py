from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


class BootstrapFormMixin:
    """Adds Bootstrap CSS classes to every field widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.RadioSelect):
                field.widget.attrs["class"] = "form-check-input me-1"
            else:
                field.widget.attrs["class"] = "form-control"


class SignUpForm(BootstrapFormMixin, UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "role")
        widgets = {"role": forms.RadioSelect}
        labels = {"role": "I want to join as"}


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    pass