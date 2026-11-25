"""
vertex/notify_rules.py

One place that says which Vertex events send a bell notification and to whom, shown on the
notification centre. It is a reading aid: the sending itself is done where each event happens.
"""

from django.utils.translation import gettext_lazy as _

# (area, when it happens, who is told)
RULES = [
    (_("Projects"), _("Someone is added to a project"), _("That person")),
    (_("Hiring requests"), _("A resource request is raised"), _("People who manage hiring requests")),
    (_("Hiring requests"), _("The status of a request changes"), _("Whoever raised it")),
    (_("Hiring requests"), _("A comment is added"), _("The other side of the conversation")),
    (_("Reviews"), _("Reviews are requested"), _("The people being reviewed, and the reviewer")),
    (_("Reviews"), _("A review is scheduled"), _("The person reviewed and the reviewer")),
    (_("Reviews"), _("An invitee answers a review meeting"), _("Whoever requested the review")),
    (_("Reviews"), _("A review outcome is submitted"), _("Whoever requested the review")),
    (_("Meeting minutes"), _("Minutes are sent"), _("The attendees who have an Ariel HR account")),
    (_("Feedback"), _("Feedback is submitted"), _("People who approve feedback")),
    (_("Feedback"), _("Feedback is sent back for revision"), _("The author")),
    (_("Feedback"), _("Feedback is published"), _("The person it is about")),
    (_("Performance"), _("A report is published"), _("The person it is about")),
    (_("Performance"), _("An improvement plan is created"), _("The person it is for, and HR")),
    (_("Appraisals"), _("A cycle is opened"), _("Every active employee")),
    (_("Appraisals"), _("A self assessment is submitted"), _("The assigned manager")),
    (_("Appraisals"), _("A manager finishes the evaluation"), _("People who release appraisals")),
    (_("Appraisals"), _("The final rating is released"), _("The employee")),
    (_("Goals"), _("A goal is assigned"), _("The employee")),
    (_("Promotions"), _("A promotion is recommended"), _("HR, to validate")),
    (_("Promotions"), _("HR validates it"), _("Leadership approvers, and whoever recommended it")),
    (_("Promotions"), _("Leadership approves it"), _("HR, to complete, and whoever recommended it")),
    (_("Promotions"), _("It is completed"), _("The employee, and whoever recommended it")),
    (_("Promotions"), _("It is rejected"), _("Whoever recommended it")),
    (_("Learning"), _("Training is assigned or suggested"), _("The employee")),
    (_("Expenses"), _("An expense that needs approval is raised"), _("Expense approvers")),
    (_("Expenses"), _("It is approved or rejected"), _("Whoever raised it")),
    (_("Expenses"), _("The daily and weekly summaries go out"), _("The recipients set in Expense settings")),
    (_("Automatic"), _("Weekdays after 16:00, no status update sent today"), _("The person, and a count to the project's managers")),
    (_("Automatic"), _("Last week of March, June, September and December, no feedback yet"), _("The reviewer of each developer and tester")),
    (_("Automatic"), _("First days of a month"), _("HR, when the draft monthly reports are ready")),
]
