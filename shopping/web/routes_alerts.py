"""In-app alert routes (sign-in-free pages, owner-scoped JSON). Moved out of the create_app route closure in
shopping/web/app.py; the behaviour is unchanged. The helpers come from the app so the rules stay in one place."""
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from watchtower import notifications as notes


def register_alerts(app, *, get_s, owner_of, require_browser_owner, render, banner) -> None:
    # in-app notifications (the table is created only by notify-migrate)
    def notes_missing() -> HTTPException:
        return HTTPException(503, "Notifications are not set up on this database. An operator must run notify-migrate on a copy first.")

    def note_json(n) -> dict:
        return {"id": n.id, "listing_id": n.listing_id, "kind": n.kind, "title": n.title, "body": n.body,
                "created_at": n.created_at.isoformat(), "read": n.read_at is not None}

    @app.get("/notifications")
    def notifications_page(request: Request, s=Depends(get_s)):
        owner = owner_of(request)
        if not notes.available(s):
            return render(request, "notifications.html", items=[], unread=0, ready=False, banner=banner(s))
        notes.generate(s, owner)  # idempotent: only events not yet recorded become rows
        return render(request, "notifications.html", items=notes.list_for(s, owner), unread=notes.unread_count(s, owner),
                      ready=True, banner=banner(s))

    @app.post("/notifications/{note_id}/read")
    def notification_read_form(note_id: int, request: Request, s=Depends(get_s)):
        if notes.available(s):
            notes.mark_read(s, owner_of(request), note_id)  # someone else's id answers the same as a missing one
            s.commit()  # commit inside the handler: a failed write must not be reported as success
        return RedirectResponse("/notifications", status_code=303)

    @app.post("/notifications/read-all")
    def notification_read_all_form(request: Request, s=Depends(get_s)):
        if notes.available(s):
            notes.mark_all_read(s, owner_of(request))
            s.commit()  # commit inside the handler: a failed write must not be reported as success
        return RedirectResponse("/notifications", status_code=303)

    @app.get("/api/v1/notifications")
    def api_notifications(request: Request, s=Depends(get_s)):
        owner = require_browser_owner(request)
        if not notes.available(s):
            raise notes_missing()
        notes.generate(s, owner)
        rows, total = notes.list_page(s, owner)
        listed_unread = sum(1 for n in rows if n.read_at is None)
        return {"unread": total, "unread_total": total, "listed": len(rows), "more": total > listed_unread,
                "items": [note_json(n) for n in rows]}

    @app.post("/api/v1/notifications/read-all")
    def api_notifications_read_all(request: Request, s=Depends(get_s)):
        owner = require_browser_owner(request)
        if not notes.available(s):
            raise notes_missing()
        marked = notes.mark_all_read(s, owner)
        s.commit()
        return {"marked": marked}

    @app.post("/api/v1/notifications/{note_id}/read")
    def api_notification_read(note_id: int, request: Request, s=Depends(get_s)):
        owner = require_browser_owner(request)
        if not notes.available(s):
            raise notes_missing()
        found = notes.mark_read(s, owner, note_id)
        s.commit()
        if not found:
            raise HTTPException(404, "notification not found")
        return {"id": note_id, "read": True}

