"""Notification model + state machine (N1).

A Notification is a personal, disposable pointer to a system event that concerns
one recipient: it carries awareness, not action. It owns no data — the truth
lives on the source object (``source_type`` + ``source_id``), and any expanded
detail is computed live from that source (see the Linked Context registry in
``notifications.services``), never stored here.

This module is the mechanism only. No trigger fires a notification yet: the live
call sites are wired by N3 (via Slice D's services) and later by Slices B and E.
The full category enum is fixed now (D-127) so those slices add no
notification-side schema — they only start calling ``notify()`` with categories
that already exist here.
"""

from django.conf import settings
from django.db import models


class Notification(models.Model):
    """One recipient's awareness of one event; read/dismiss status is per record."""

    class Status(models.TextChoices):
        UNREAD = "unread", "Unread"
        READ = "read", "Read"
        DISMISSED = "dismissed", "Dismissed"

    class Category(models.TextChoices):
        # The full documented enum, fixed now (D-127), so later slices (B/E) add
        # no notification-side schema. Most values are dormant this phase — their
        # source object is a Phase 2+ shell with no live event — but they are
        # shipped now so a future trigger only has to *call* notify(), never
        # migrate. Grouped by domain cluster, per notification/definition.md.

        # User / Clearance
        CLEARANCE_CHANGED = "clearance_changed", "Clearance changed"
        ACCOUNT = "account", "Account"  # name/DOB change; approved identity CRs
        # ClearanceRequest workflow (Phase 2+)
        CLR_PENDING_MANAGER = "clr_pending_manager", "Clearance request pending manager"
        CLR_PENDING_ADMIN = "clr_pending_admin", "Clearance request pending admin"
        CLR_MANAGER_APPROVED = "clr_manager_approved", "Clearance request manager-approved"
        CLR_APPROVED = "clr_approved", "Clearance request approved"
        CLR_REJECTED = "clr_rejected", "Clearance request rejected"
        CLR_CANCELLED = "clr_cancelled", "Clearance request cancelled"
        # ChangeRequest
        CR_REJECTED = "cr_rejected", "Change request rejected"
        # Station
        STATION_BREAKDOWN = "station_breakdown", "Station breakdown"
        STATION_MAINTENANCE = "station_maintenance", "Station maintenance"
        STATION_OFFLINE = "station_offline", "Station offline"
        STATION_AVAILABLE = "station_available", "Station available"
        STATION_RETIRED = "station_retired", "Station retired"
        CAPABILITY_LOST = "capability_lost", "Capability lost"
        # Operation
        OP_ASSIGNED = "op_assigned", "Operation assigned"
        OP_RESCHEDULED = "op_rescheduled", "Operation rescheduled"
        OP_UNASSIGNED = "op_unassigned", "Operation unassigned"
        OP_BLOCKED = "op_blocked", "Operation blocked"
        DUE_DATE_BREACH = "due_date_breach", "Due-date breach"
        OP_PARTIAL = "op_partial", "Operation partially complete"
        RESCHEDULE_SUGGESTION = "reschedule_suggestion", "Reschedule suggestion"
        # LeaveRequest
        LEAVE_SUBMITTED = "leave_submitted", "Leave submitted"
        LEAVE_APPROVED = "leave_approved", "Leave approved"
        LEAVE_SLOTS_AFFECTED = "leave_slots_affected", "Leave affects schedule"
        LEAVE_REJECTED = "leave_rejected", "Leave rejected"
        LEAVE_REVOKED = "leave_revoked", "Leave revoked"
        LEAVE_SCHEDULE_REVIEW = "leave_schedule_review", "Leave schedule review"
        LEAVE_CANCELLED = "leave_cancelled", "Leave cancelled"
        # Order
        ORDER_READY = "order_ready", "Order ready"
        ORDER_CANCELLED = "order_cancelled", "Order cancelled"
        ORDER_DELIVERED = "order_delivered", "Order delivered"
        # WorkItem
        WORK_ITEM_ASSIGNED = "work_item_assigned", "Work item assigned"
        WORK_ITEM_UNASSIGNED = "work_item_unassigned", "Work item unassigned"
        WORK_ITEM_COMPLETED = "work_item_completed", "Work item completed"
        WORK_ITEM_DISMISSED = "work_item_dismissed", "Work item dismissed"
        WORK_ITEM_SOURCE_CHANGED = "work_item_source_changed", "Work item source changed"
        WORK_ITEM_CANCELLED = "work_item_cancelled", "Work item cancelled"
        # Stock
        STOCK_OUT = "stock_out", "Stock out"
        STOCK_REPLENISHED = "stock_replenished", "Stock replenished"
        # PurchaseOrder
        PO_ARRIVED = "po_arrived", "Purchase order arrived"
        PO_CANCELLED = "po_cancelled", "Purchase order cancelled"
        # Issue / Report
        REPORT_RAISED = "report_raised", "Report raised"
        REPORT_RESOLVED = "report_resolved", "Report resolved"
        REPORT_DISMISSED = "report_dismissed", "Report dismissed"
        # UserInvitation
        INVITE_ACCEPTED = "invite_accepted", "Invitation accepted"
        INVITE_EXPIRED = "invite_expired", "Invitation expired"
        # Messaging (reserved)
        MESSAGE = "message", "Message"

    # A personal, disposable pointer owned by its recipient: CASCADE, so deleting
    # the user clears their notifications (they carry no independent value). There
    # is deliberately NO workshop FK — a Notification is scoped transitively via
    # its recipient (every query is ``recipient=request.user``), never
    # workshop-wide (KI-021 tenancy: scope through the owning identity, not a
    # redundant column that could disagree with it).
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=255)
    body = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNREAD
    )
    # Per-recipient personal flags, independent of ``status``: a record may be
    # pinned or flagged important in ANY state (including dismissed), and toggling
    # a flag never changes status. Deliberately fields, not states — they express
    # personal prioritisation, orthogonal to the attention lifecycle.
    pinned = models.BooleanField(default=False)
    important = models.BooleanField(default=False)
    category = models.CharField(max_length=32, choices=Category.choices)
    # Generic pointer to the triggering object: model name + stringified pk. Both
    # NULL for a system-wide notification with no single source object. Nothing
    # about the source is stored beyond this pointer — Linked Context is computed
    # live from it (notifications.services.linked_context).
    source_type = models.CharField(max_length=100, null=True, blank=True)
    source_id = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The unread nav badge is filter(recipient=?, status="unread"), run on
            # every authenticated request (N2). Index the pair it counts on.
            models.Index(fields=["recipient", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.category}: {self.title}"

    # -- State machine ----------------------------------------------------- #
    # unread <-> read; unread/read -> dismissed. ``dismissed`` is terminal and
    # lossless (the triggering event is untouched on its source). Badge count is
    # ``status == unread`` only.

    def mark_read(self) -> None:
        """unread -> read. Idempotent when already read; dismissed is terminal."""
        self._reject_if_dismissed()
        if self.status != self.Status.READ:
            self.status = self.Status.READ
            self.save(update_fields=["status"])

    def mark_unread(self) -> None:
        """read -> unread (re-flag for own attention). Idempotent; dismissed is terminal."""
        self._reject_if_dismissed()
        if self.status != self.Status.UNREAD:
            self.status = self.Status.UNREAD
            self.save(update_fields=["status"])

    def dismiss(self) -> None:
        """unread/read -> dismissed (terminal). Idempotent when already dismissed."""
        if self.status != self.Status.DISMISSED:
            self.status = self.Status.DISMISSED
            self.save(update_fields=["status"])

    def _reject_if_dismissed(self) -> None:
        # ``dismissed`` is terminal — there is no transition back out. This raise
        # is an N1 implementation decision (the domain calls dismissed "terminal"
        # but does not itself mandate an exception); N2's UI must therefore never
        # offer mark-read/unread on a dismissed row, consistent with "Mark all
        # read … dismissed notifications are unaffected".
        if self.status == self.Status.DISMISSED:
            raise ValueError(
                "A dismissed notification is terminal; it cannot be marked read or unread."
            )

    # -- Personal flags (independent of status, valid in any state) -------- #

    def set_pinned(self, value: bool) -> None:
        """Set the ``pinned`` flag; never changes ``status`` (valid in any state)."""
        if self.pinned != value:
            self.pinned = value
            self.save(update_fields=["pinned"])

    def set_important(self, value: bool) -> None:
        """Set the ``important`` flag; never changes ``status`` (valid in any state)."""
        if self.important != value:
            self.important = value
            self.save(update_fields=["important"])
