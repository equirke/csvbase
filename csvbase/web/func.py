import re
import functools
from datetime import datetime, timezone
from typing import Optional, Callable, Mapping, Tuple, Any
from logging import getLogger
from urllib.parse import urlsplit, urlparse

from sqlalchemy.orm import Session

import werkzeug
import werkzeug.exceptions
from werkzeug.wrappers.response import Response
from flask import session as flask_session
from flask import request, g, current_app, Request, redirect as unsafe_redirect, flash
from flask_babel import get_locale, dates

from .. import exc, sentry, svc
from ..value_objs import User

logger = getLogger(__name__)


def is_browser() -> bool:
    # FIXME: this should call negotiate_content_type
    # bit of content negotiation magic
    accepts = werkzeug.http.parse_accept_header(request.headers.get("Accept"))
    best = accepts.best_match(["text/html", "text/csv"], default="text/csv")
    return best == "text/html"


def set_current_user(user: User) -> None:
    g.current_user = user

    # This is duplication but very convenient for jinja templates
    g.current_username = user.username

    sentry.set_user(user)


def get_current_user() -> Optional[User]:
    """Return the current user.  This function exists primarily for type
    checking reasons - to avoid accidental assumptions that g.current_user is
    present.

    """
    if hasattr(g, "current_user"):
        return g.current_user
    else:
        return None


def get_current_user_or_401() -> User:
    """If there is no current user, raise NotAuthenticatedException.  If the
    user is using a browser, this will redirect to the registration page.

    """
    current_user = get_current_user()
    if current_user is None:
        raise exc.NotAuthenticatedException()
    return current_user


def reverse_url_for(
    url: str, method: str = "GET"
) -> Optional[Tuple[Callable, Mapping]]:
    """Returns the view function that would handle a given url"""
    adapter = current_app.url_map.bind_to_environ(request.environ)
    path = urlsplit(url)[2]
    try:
        view_func, view_args = adapter.match(path, method=method)
    except werkzeug.exceptions.HTTPException:
        logger.warning("'%s' didn't match any routes", url)
        return None
    return current_app.view_functions[view_func], view_args


def user_timezone_or_utc() -> str:
    user = get_current_user()
    if user is not None:
        return user.timezone
    else:
        return "UTC"


# FIXME: upstream this
def format_timedelta(
    datetime_or_timedelta,
    granularity: str = "second",
    add_direction=False,
    threshold=0.85,
):
    """Format the elapsed time from the given date to now or the given
    timedelta.

    This function is also available in the template context as filter
    named `timedeltaformat`.
    """
    if isinstance(datetime_or_timedelta, datetime):
        is_aware = (
            datetime_or_timedelta.tzinfo is not None
            and datetime_or_timedelta.tzinfo.utcoffset(datetime_or_timedelta)
            is not None
        )
        if is_aware:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.utcnow()
        datetime_or_timedelta = now - datetime_or_timedelta
    return dates.format_timedelta(
        datetime_or_timedelta,
        granularity,
        threshold=threshold,
        add_direction=add_direction,
        locale=get_locale(),
    )


_URL_REGEX = re.compile("https?://[^ ]+$")


@functools.lru_cache
def is_url(text_string: str) -> bool:
    """Returns true if the text string is a url.

    This function is used in the templating to decide whether we should turn
    something into a hyperlink.  It's fairly conservative - the url needs to be
    fully qualified and start with http:// or https://

    """
    # as an optimisation, make sure it vaguely looks like a fully qualified url
    # before even trying to parse it
    if _URL_REGEX.match(text_string):
        try:
            parsed_url = urlparse(text_string)
            return True
        except ValueError:
            pass

    return False


def safe_redirect(to_raw: str) -> Response:
    """Redirect to a url, but only if it matches our server name.

    Intended for untrusted user-supplied input (ie: whence url params)."""
    to = urlparse(to_raw)
    base_url = urlparse(request.base_url)
    # relative link
    if to.scheme == "" and to.netloc == "":
        return unsafe_redirect(to_raw)
    elif to.scheme == base_url.scheme and to.netloc == base_url.netloc:
        return unsafe_redirect(to_raw)
    else:
        raise exc.InvalidRequest(f"won't redirect outside of {request.base_url}")


def register_and_sign_in_new_user(sesh: Session) -> User:
    """Registers a new user and signs them in if the registration succeeds."""
    form = request.form
    new_user = svc.create_user(
        sesh,
        current_app.config["CRYPT_CONTEXT"],
        form["username"],
        form["password"],
        form.get("email"),
    )
    sesh.commit()
    sign_in_user(new_user)
    flash("Account registered")
    return new_user


def sign_in_user(user: User, session: Optional[Any] = None) -> None:
    """Sets the current user and sets a cookie to keep them logged in across
    requests.

    """
    set_current_user(user)

    if session is None:
        session = flask_session
    session["user_uuid"] = user.user_uuid
    # Make it last for 31 days
    session.permanent = True
