"""Submission matrix, separate from permissive draft validation."""
from django.core.exceptions import ValidationError

ANSWER_TOPICS = ("benefits", "per_diem", "required_skills", "preferred_skills",
                 "required_experience", "preferred_experience", "required_credentials",
                 "preferred_credentials", "conditions", "equipment", "screening", "other")


def submission_errors(revision):
    r = revision
    errors = {}

    def require(field, message="Answer this topic before submitting for review."):
        value = getattr(r, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[field] = message

    for field in ("title", "trade", "description", "worker_quantity", "employment_type",
                  "location_mode", "pay_response", "pay_basis", "shift", "schedule_details", "hours_mode",
                  "start_mode", "duration_response", "primary_contact", "hiring_timeline"):
        require(field)
    if r.employment_type == "other":
        require("employment_details", "Describe the employment type.")
    if r.location_mode == "location":
        require("location", "Choose an active customer location.")
        if r.location_id and (not r.location.is_active or r.location.organization_id != r.organization_id):
            errors["location"] = "Choose an active location from this organization."
        for field in ("location_name", "address_line1", "city", "location_state", "country"):
            require(field, "Complete this location field before submission.")
    elif r.location_mode == "custom":
        for field in ("location_name", "address_line1", "city", "location_state", "country"):
            require(field, "Provide a meaningful work location.")
    elif r.location_mode == "remote":
        require("location_details", "Describe the remote work arrangement and geographic requirements.")
    if r.pay_response == "provided":
        require("pay_basis", "Choose how the job is paid.")
        require("pay_minimum", "Enter the lowest pay.")
        require("pay_currency", "Enter the lowest pay.")
        if r.pay_basis in {"hour", "year"}:
            require("pay_frequency", "Choose how often workers are paid.")
        if r.pay_basis == "other":
            require("pay_details", "Describe how the job is paid.")
    elif r.pay_response == "not_applicable":
        require("pay_details", "Describe how the job is paid, or when pay will be decided.")
        if r.pay_minimum is not None or r.pay_maximum is not None:
            errors["pay_minimum"] = "Remove amounts when compensation is not applicable."
    for topic in ANSWER_TOPICS:
        response = getattr(r, topic + "_response")
        require(topic + "_response")
        if response in {"provided", "not_applicable"}:
            require(topic + "_details", "Provide details or explain why this is not applicable.")
    for topic in ("overtime", "travel", "overnight"):
        require(topic + "_response")
        if getattr(r, topic + "_response") in {"yes", "not_applicable"}:
            require(topic + "_details", "Provide details or explain why this is not applicable.")
    if r.per_diem_response != "provided" and r.per_diem_amount is not None:
        errors["per_diem_amount"] = "Remove the amount when per diem is not provided."
    for topic in ("required_experience", "preferred_experience"):
        if getattr(r, topic + "_response") != "provided" and getattr(r, topic + "_years") is not None:
            errors[topic + "_years"] = "Remove years when experience is not required/provided."
    if r.hours_mode == "specified":
        require("hours_per_week", "Provide positive weekly hours (maximum 168).")
    elif r.hours_mode == "variable" and r.hours_per_week is not None:
        errors["hours_per_week"] = "Remove the fixed hours for an explained variable schedule."
    if r.start_mode == "date":
        require("start_date", "Provide the start date.")
    elif r.start_mode == "timeframe":
        require("start_timeframe", "Describe the start timeframe.")
    if r.duration_response in {"provided", "not_applicable"}:
        require("duration_details", "Describe the duration or explain N/A.")
    if r.primary_contact_id and (not r.primary_contact.is_active or r.primary_contact.organization_id != r.organization_id):
        errors["primary_contact"] = "Choose an active business contact from this organization."
    return errors


def require_submittable(revision):
    revision.full_clean()
    errors = submission_errors(revision)
    if errors:
        raise ValidationError(errors)
