# Run the location examples

Run an in-memory city example to select streets and calculate weighted block scores. The examples also resolve existing buffer records and summarize projected areas. Put the example directory on Python’s import path, then load `location_plugin:create_plugin`. Use the public ID selections in `requests.json` with `LocalRunner.run` or job submission.

The companion `location_plugin.capabilities` example registers related metrics with private calculations. Follow the [location example usage guide](USAGE.md) to run it locally or exercise the client without services.
