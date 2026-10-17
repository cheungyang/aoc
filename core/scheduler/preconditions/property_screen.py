"""property_screen: property-scout's weekly scout runs only when new listings match."""
from core.scheduler.preconditions.scan import Scan
from core.scheduler.spec import ScheduleContext, WorkDecision


class PropertyScreen(Scan):
    """New listings match the real_estate_scout criteria.

    Findings: the new matches, with the listing tool's numbers.
    """
    type_name = "property_screen"

    def scan(self, ctx: ScheduleContext) -> WorkDecision:
        raise NotImplementedError("property_screen is not built yet")
