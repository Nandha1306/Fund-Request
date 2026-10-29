"""Business modules.  Each one owns its tables and exposes: models, schemas, router, service, policies.

Importing this package registers every model on ``Base.metadata`` (Alembic relies on it),
so each module's ``models`` is imported here once it exists.
"""

# noqa: F401 — side-effect imports register every table on Base.metadata
from app.modules.accounts import models as _accounts  # noqa: F401
from app.modules.audit import models as _audit  # noqa: F401
from app.modules.expenses import models as _expenses  # noqa: F401
from app.modules.finance import models as _finance  # noqa: F401
from app.modules.fund_requests import models as _fund_requests  # noqa: F401
from app.modules.projects import models as _projects  # noqa: F401
