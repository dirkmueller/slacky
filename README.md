# slacky

A Slack bot that monitors the [SUSE BCI](https://github.com/SUSE/bci/discussions) publishing/build pipelines for common failures. The code
is only intended for this one purpose. Making it reusable outside that
target is not in scope.

## Setup

1.  Install:
    ```bash
    pipx install .
    ```
2.  Configure the bot by creating a ~/.config/slacky file
    ```
    [DEFAULT]
    slack_trigger_url = ...

    [openqa]
    host = https://openqa.opensuse.org

    [obs]
    host = https://build.opensuse.org
    container_build_re = $projectstomonitorforbuild
    container_publish_re = $containerprojectstomonitorforpublish
    repo_publish_re = $repoprojectstomonitorforpublish```

3.  Run the bot
    ```bash
    python3 slacky.py
    ```

## Dependencies

*   Python 3.11+
*   `pika`
*   `requests`

## Contributing

Contributions are welcome! Please send PRs or file issues. Make sure the
ruff lint is passing as well as appropriate unit tests are added for complex
changes.

## License

[GPLv2](./LICENSE)
