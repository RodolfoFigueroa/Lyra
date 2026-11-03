# Run Lyra with plugins

Use this example to run Lyra and the example plugin with Docker Compose. Follow [Deploy Lyra](../../docs/src/content/docs/operate/deployment.mdx) to configure services and start the containers.

The default `CITY_PLUGIN_SOURCE=../city-plugin` uses the city project in this checkout, including changes from the tutorial. To deploy another copy, set that variable in `.env` to its absolute directory. Export its public factory and regenerate its manifest before rebuilding. The city provider needs no resource settings or secrets.

Choose the guide for your next task:

- [Configure plugin resources](../../docs/src/content/docs/operate/resources.md)
- [Develop with editable sources](../../docs/src/content/docs/plugins/development.md)
- [Add another plugin checkout](../../docs/src/content/docs/plugins/development.md#add-another-checkout)
- [Update a running deployment](../../docs/src/content/docs/operate/updates.md)
