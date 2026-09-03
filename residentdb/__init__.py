"""
The encrypted resident store.

A Django app in the structural sense only -- there is no web server, no views,
no URLs. Django is here for its ORM, its migration system, and its field API,
which is what makes column-level encryption clean to express.

Import `store` (top level) rather than this package directly; it handles Django
configuration before any model is touched.
"""
