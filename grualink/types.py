"""Request and response shapes of the public API. Field names are the wire names (snake_case).

These are ``TypedDict`` definitions: at run time the values are the plain dictionaries parsed from JSON.
Unknown fields in a response are forward compatible: ignore what you do not know.
"""

from __future__ import annotations

from typing import Literal, TypedDict

VehicleType = Literal["auto", "camioneta", "motocicleta", "camion", "otro"]
ServiceCategory = Literal["vial", "colision"]
TowTruckClass = Literal["A", "B", "C", "D"]
Accessory = Literal[
    "dolly",
    "winch",
    "cadenas",
    "eslingas",
    "patines",
    "rampas",
    "barra_tiro",
    "extension_pluma",
    "gato_hidraulico",
    "pasacorriente",
    "compresor",
    "luces_remolque",
]
Mode = Literal["test", "live"]


class _PlaceRequired(TypedDict):
    #: Required, up to 500 characters.
    address: str


class Place(_PlaceRequired, total=False):
    #: Latitude and longitude go together. Without pickup and destination coordinates there is no
    #: estimated price.
    lat: float
    lng: float


class Contact(TypedDict):
    #: Up to 120 characters.
    name: str
    #: International format (+52...); 10 digits are taken as a Mexican number.
    phone: str


class Vehicle(TypedDict, total=False):
    make: str
    model: str
    year: int
    color: str
    plate: str
    type: VehicleType


class ServiceInput(TypedDict, total=False):
    reason: str
    category: ServiceCategory
    required_class: TowTruckClass
    required_accessories: list[Accessory]


class _ServiceRequestInputRequired(TypedDict):
    contact: Contact
    pickup: Place


class ServiceRequestInput(_ServiceRequestInputRequired, total=False):
    """Body of ``POST /service-requests``. Unknown fields are rejected with ``validation_error``."""

    #: Your own reference (claim or case number), up to 64 characters.
    external_ref: str
    vehicle: Vehicle
    destination: Place
    service: ServiceInput
    #: Up to 1,000 characters and 20 lines.
    notes: str


ServiceRequestStatus = Literal[
    "received",
    "quoted",
    "confirmed",
    "in_progress",
    "completed",
    "rejected",
    "cancelled",
    "expired",
]

UnpricedReason = Literal[
    "missing_coordinates",
    "missing_destination",
    "no_zones_configured",
    "missing_vehicle_class",
]


class Tax(TypedDict):
    name: str
    #: ``tax`` (added) or ``retention`` (subtracted).
    type: Literal["tax", "retention"]
    rate: float
    amount: float


class EstimatedPrice(TypedDict):
    currency: Literal["MXN"]
    subtotal: float
    taxes: list[Tax]
    tax_total: float
    retention_total: float
    total: float


class _QuoteRequired(TypedDict):
    #: ``None`` when the request could not be priced automatically; see ``unpriced_reason``.
    estimated_price: EstimatedPrice | None
    #: Always ``False``: the estimate never includes extras such as tolls.
    includes_extras: bool
    unpriced_reason: UnpricedReason | None


class Quote(_QuoteRequired, total=False):
    approximate_distance: bool
    note: str


class RegisteredService(TypedDict):
    folio: str | None
    status: str


class Truck(TypedDict):
    economic_number: str | None
    plate: str | None


class Assignment(TypedDict):
    operator_first_name: str | None
    truck: Truck | None


class Timeline(TypedDict, total=False):
    on_the_way_at: str
    on_site_at: str
    transport_started_at: str
    completed_at: str


class ServiceRequest(TypedDict):
    id: str
    object: Literal["service_request"]
    status: ServiceRequestStatus
    mode: Mode
    external_ref: str | None
    created_at: str
    resolved_at: str | None
    rejection_reason: str | None
    service: RegisteredService | None
    quote: Quote
    assignment: Assignment | None
    timeline: Timeline | None


class ServiceRequestList(TypedDict):
    object: Literal["list"]
    #: Up to 20 requests, most recent first.
    data: list[ServiceRequest]


class Health(TypedDict):
    status: str
    version: str


class WebhookEventData(TypedDict, total=False):
    #: Previous status on status events; ``None`` on assigned, on_the_way and on_site.
    previous_status: ServiceRequestStatus | None
    #: Same representation as ``GET /service-requests/{id}`` at that moment.
    service_request: ServiceRequest
    #: Only on ``webhook.test``.
    message: str


class WebhookEvent(TypedDict):
    """Body of a webhook delivery."""

    #: Same value as the ``X-GruaLink-Delivery`` header and on every retry. Use it to deduplicate.
    id: str
    object: Literal["event"]
    #: ``service_request.quoted``, ``service_request.confirmed``, ... or ``webhook.test``. Ignore the
    #: types you do not know.
    type: str
    mode: Mode
    created_at: str
    data: WebhookEventData
