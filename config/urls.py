from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("matches.urls")),
]

if settings.DEBUG:
    # In development Django serves the uploaded photos itself. Online, the web server would do it.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
