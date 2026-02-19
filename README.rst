Pretix Google Events (StructuredData)
=====================================

A plugin for `pretix`_ that automatically injects schema.org structured data (JSON-LD) into event pages. Improves search engine visibility and enables rich event information in Google Search results.

**Features:**
- Automatic JSON-LD schema.org Event markup generation
- Customizable event metadata (name, description, location, organizer, performer)
- Per-ticket pricing and availability overrides
- Support for online, offline, and mixed-mode events
- Multilingual support with caching
- URL validation and security best practices

Installation & Development
--------------------------

1. Ensure you have a working `pretix development setup`_.
2. Clone this repository.
3. Activate the virtual environment for pretix development.
4. Run ``python setup.py develop`` to register the plugin.
5. Run ``make`` to compile translations.
6. Restart your pretix server and enable the plugin in the 'plugins' tab.

Code Quality
~~~~~~~~~~~~

This project enforces code style rules via flake8, isort, and black::

    pip install flake8 isort black

Verify compliance::

    black --check .
    isort -c .
    flake8 .

Auto-fix issues::

    isort .
    black .

Install pre-commit hooks::

    .install-hooks


License
-------

Copyright 2026 Daniel Malik <mail@fronbasal.de>

Released under the terms of the Apache License 2.0



.. _pretix: https://github.com/pretix/pretix
.. _pretix development setup: https://docs.pretix.eu/en/latest/development/setup.html
