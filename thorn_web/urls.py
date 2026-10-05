"""Asocia cada direccion HTTP con su funcion de respuesta asincrona."""

from django.urls import path

from api import views

urlpatterns = [
    path("", views.health, name="home"),
    path("api/v1/health/", views.health, name="health"),
    path("api/v1/status/", views.status, name="status"),
    path("api/v1/events/", views.publish_event, name="publish_event"),
]
