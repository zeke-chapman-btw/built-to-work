"""One plain-text layout for every account/link email, so they all read the same."""

SIGN_OFF = "The Built to Work team"


def link_email(intro, action, url, *, hours=24, single_use=True):
    """Friendly plain-text body for an email that carries a private link."""
    expiry = f"This link works for {hours} hours" + (" and can only be used once." if single_use else ".")
    return (
        "Hi,\n\n"
        f"{intro}\n\n"
        f"{action}:\n{url}\n\n"
        f"{expiry} Please don't share it with anyone.\n\n"
        "Didn't expect this email? You can ignore it, or contact BTW if you have questions.\n\n"
        f"Thanks,\n{SIGN_OFF}\n"
    )
