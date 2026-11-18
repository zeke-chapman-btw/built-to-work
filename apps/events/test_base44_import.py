"""Events → Import from base44."""
from datetime import date
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event

HEAD = ("name,venue_name,address,city,state,zip,event_type,event_type_custom,setup_date,setup_arrival_time,start_date,start_time,"
        "end_date,end_time,arrival_home_date,drivers_needed,staff_needed,daily_needs,staff_signup_deadline,housing_type,hotel_name,"
        "housing_address,housing_city,housing_state,housing_zip,housing_info,contact_name,contact_email,contact_phone,notes,status,"
        "readiness,latitude,longitude,id,created_date,updated_date,created_by_id,is_sample\n")
ROWS = (
    '"Expo Day","Classic Center","300 N Thomas St","Athens","GA","30601","other","Employee Event","2030-12-03","14:00","2030-12-04","08:00",'
    '"2030-12-05","14:00","2030-12-05","1","4","[{""date"":""2030-12-04"",""drivers_needed"":1,""staff_needed"":4}]","2030-11-25T23:59",'
    '"airbnb","Lake House","1 Main","Athens","GA","30601","Entry code is 7357. Wifi password abc","Pat Lee","pat@example.test","(478) 555-0101",'
    '"Bring the trailer.","upcoming","finalized","33.96009075089491","-83.3726597775572","a1","","","","false"\n'
    '"Test Event","","","","","","","","","","2030-09-23","","2030-09-24","","","1","4","","","","","","","","","","","","","","archived","","","","t1","","","","false"\n'
    '"Southeast Georgia Skills Challenege","","","Statesboro","GA","","skills_challenge","","","","2030-11-11","08:00","2030-11-11","13:00","","1","4","","","","","","","","","","","","","","completed","","","","s1","","","","false"\n'
)


class EventImportTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("ev-import-admin", "a@example.test", "x-test-only")
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        tz = ZoneInfo("America/New_York")
        Event.objects.create(name="Southeast Georgia Skills Challenge", status="completed",
                             start_at=timezone.datetime(2030, 11, 11, 8, tzinfo=tz), end_at=timezone.datetime(2030, 11, 11, 13, tzinfo=tz))

    def test_check_then_add_with_staffing_needs_and_codes_left_out(self):
        url = reverse("events:import-base44")
        self.client.post(url, {"action": "check", "file": SimpleUploadedFile("e.csv", (HEAD + ROWS).encode())})
        page = self.client.get(url).content.decode()
        self.assertIn("1 to add", page)
        self.assertIn("Housing note has codes", page)
        self.assertIn("Already here", page)  # base44 typo still matches the event that's here
        self.client.post(url, {"action": "save"})
        event = Event.objects.get(name="Expo Day")
        self.assertEqual((event.status, event.event_type, event.event_type_other), ("upcoming", "other", "Employee Event"))
        self.assertEqual(timezone.localtime(event.start_at, ZoneInfo("America/New_York")).hour, 8)
        self.assertEqual(event.city, "Athens")
        self.assertIn("Lake House", event.housing_information)
        self.assertNotIn("7357", event.housing_information)
        plan = event.staffing_plan
        self.assertEqual((plan.required_staff, plan.required_drivers), (4, 1))
        self.assertEqual(list(plan.daily_needs.values_list("date", "required_staff")), [(date(2030, 12, 4), 4)])
        self.assertEqual(Event.objects.count(), 2)

    def test_keeping_housing_codes_is_a_choice(self):
        url = reverse("events:import-base44")
        self.client.post(url, {"action": "check", "file": SimpleUploadedFile("e.csv", (HEAD + ROWS).encode())})
        self.client.post(url, {"action": "save", "keep_secret_notes": "1"})
        self.assertIn("7357", Event.objects.get(name="Expo Day").housing_information)

    def test_only_admins_and_managers(self):
        staff = get_user_model().objects.create_user("ev-import-staff", password="x-test-only", is_staff=True)
        self.client.force_login(staff, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("events:import-base44")).status_code, 403)
