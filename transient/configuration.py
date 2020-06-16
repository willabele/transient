"""Supports the creation and validation of Transient-run configurations
"""

import logging
import toml

from marshmallow import Schema, fields, post_load, pre_load, ValidationError

from typing import Any, Dict, List, MutableMapping


class ConfigFileParsingError(Exception):
    """Raised when a parsing error is encountered while loading the
       configuration file
    """

    pass


class ConfigFileOptionError(Exception):
    """Raised when an invalid configuration option value is encountered in the
       configuration file
    """

    pass


class CLIArgumentError(Exception):
    """Raised when an invalid command line argument is encountered
    """

    pass


class Config(Dict[Any, Any]):
    """Creates an argument dictionary that allows dot notation to access values

    Example:

        >>> args = Config({'arg1': 1, 'arg2': 2})
        >>> args['arg1'] == args.arg1

    """

    def __getattr__(self, attr: Any) -> Any:
        return self.get(attr)

    def __setattr__(self, key: Any, value: Any) -> None:
        self.__setitem__(key, value)

    def __delattr__(self, item: Any) -> None:
        self.__delitem__(item)


class _TransientConfigSchema(Schema):
    """Defines a common schema for the Transient configurations and validates
       the fields during deserialization
    """

    image = fields.List(fields.Str(), missing=[])
    image_backend = fields.Str(allow_none=True)
    image_frontend = fields.Str(allow_none=True)
    name = fields.Str(allow_none=True)

    # marshmallow's decorator pre_load() is untyped, forcing
    # remove_unset_options() to be untyped. Therefore, we ignore it to
    # silence the type checker
    @pre_load  # type: ignore
    def remove_unset_options(
        self, config: Dict[Any, Any], **kwargs: Dict[Any, Any]
    ) -> Dict[Any, Any]:
        """Removes any option that was not set in the command line
        """
        config_without_unset_options = {}
        for option, value in config.items():
            if _option_was_set_in_cli(config[option]):
                config_without_unset_options[option] = value

        return config_without_unset_options

    # marshmallow's decorator post_load() is untyped, forcing create_args()
    # to be untyped. Therefore, we ignore it to silence the type checker
    @post_load  # type: ignore
    def create_config(self, data: Dict[Any, Any], **kwargs: Dict[Any, Any]) -> Config:
        """Returns the Config dictionary after a schema is loaded and validated
        """
        return Config(**data)


class _TransientDeleteConfigSchema(_TransientConfigSchema):
    """Defines the schema for the Transient-delete configuration and validates
       the fields during deserialization
    """

    force = fields.Bool(missing=False)


class _TransientListConfigSchema(_TransientConfigSchema):
    """Defines the schema for the Transient-list configuration and validates
       the fields during deserialization

       Note that this class is a wrapper to maintain symmetry with the other
       schemas.
    """

    pass


class _TransientRunConfigSchema(_TransientConfigSchema):
    """Defines the schema for the Transient-run configuration and validates the
       fields during deserialization
    """

    config = fields.Str(allow_none=True)
    copy_in_before = fields.List(fields.Str(), missing=[])
    copy_out_after = fields.List(fields.Str(), missing=[])
    copy_timeout = fields.Int(allow_none=True)
    prepare_only = fields.Bool(missing=False)
    qemu_args = fields.List(fields.Str(), allow_none=True)
    qmp_timeout = fields.Int(missing=10, allow_none=True)
    shutdown_timeout = fields.Int(missing=20)
    ssh_command = fields.Str(allow_none=True)
    ssh_bin_name = fields.Str(missing="ssh", allow_none=True)
    ssh_port = fields.Int(allow_none=True)
    ssh_timeout = fields.Int(missing=90, allow_none=True)
    ssh_user = fields.Str(missing="vagrant", allow_none=True)
    ssh_console = fields.Bool(missing=False)
    ssh_with_serial = fields.Bool(missing=False)
    shared_folder = fields.List(fields.Str(), missing=[])


def _option_was_set_in_cli(option: Any) -> bool:
    """Returns True if an option was set in the command line
    """
    if option is None or option == () or option is False:
        return False

    return True


def _get_line_number_of_option_in_config_file(option: str, config_file_path: str) -> int:
    """Returns the line number where the option is found in the config file
    """
    with open(config_file_path) as config_file:
        for line_number, line in enumerate(config_file, start=1):
            if option in line:
                return line_number

    return -1


def _log_error_message_for_invalid_config_file_option(
    error: ValidationError, config_file_path: str
) -> None:
    """Logs an error message regarding an invalid configuration in the config file
       and its associated line number
    """
    for invalid_option in error.messages:

        # Revert the option to its preformatted state
        invalid_option = invalid_option.replace("_", "-")

        line_number = _get_line_number_of_option_in_config_file(
            invalid_option, config_file_path
        )

        if line_number != -1:
            logging.error(
                f"Invalid option on line {line_number} in configuration file: {invalid_option}",
            )
        else:
            logging.error(f"Invalid option in configuration file: {error}")


def _replace_hyphens_with_underscores_in_dict_keys(
    dictionary: Dict[Any, Any]
) -> Dict[Any, Any]:
    """Replaces hyphens in the dictionary keys with underscores

       This is the expected key format for _TransientConfigSchema
    """
    final_dict = {}
    for k, v in dictionary.items():
        # Perform this method recursively for sub-directories
        if isinstance(dictionary[k], dict):
            new_v = _replace_hyphens_with_underscores_in_dict_keys(v)
            final_dict[k.replace("-", "_")] = new_v
        else:
            final_dict[k.replace("-", "_")] = v

    return final_dict


def _parse_config_file(config_file_path: str) -> MutableMapping[str, Any]:
    """Parses the given config file and returns the contents as a dictionary
    """
    with open(config_file_path) as file:
        config_file = file.read()

    try:
        parsed_config_file = toml.loads(config_file)
    except RuntimeError as error:
        logging.error(f"Failed to parse configuration file: {error}")
        raise ConfigFileParsingError(error)

    return parsed_config_file


def _load_config_file(config_file_path: str) -> Config:
    """Reformats and validates the config file
    """
    parsed_config_file = _parse_config_file(config_file_path)

    reformatted_config = _replace_hyphens_with_underscores_in_dict_keys(
        parsed_config_file["transient"]
    )

    reformatted_config["qemu_args"] = parsed_config_file["qemu"]["qemu-args"]

    transient_config_schema = _TransientRunConfigSchema()

    try:
        config: Config = transient_config_schema.load(reformatted_config)
    except ValidationError as error:
        _log_error_message_for_invalid_config_file_option(error, config_file_path)
        raise ConfigFileOptionError(error)

    return config


def _consolidate_cli_args_and_config_file(cli_args: Dict[Any, Any]) -> Dict[Any, Any]:
    """Consolidates and returns the CLI arguments and the configuration file

       Note that the CLI arguments take precedence over the configuration file
    """
    config = _load_config_file(cli_args["config"])

    for option, value in config.items():
        if (
            option == "qemu_args" and cli_args[option] == ()
        ) or not _option_was_set_in_cli(cli_args[option]):
            cli_args[option] = value

    return cli_args


def _create_transient_config_with_schema(
    config: Dict[Any, Any], schema: _TransientConfigSchema
) -> Config:
    """Creates and validates the Config to be used by Transient given the
       CLI arguments and schema
    """
    try:
        validated_config: Config = schema.load(config)
    except ValidationError as error:
        logging.error(f"Invalid command line arguments given: {error}")
        raise CLIArgumentError(error)

    return validated_config


def create_transient_list_config(cli_args: Dict[Any, Any]) -> Config:
    """Creates and validates the Config to be used by Transient-list given the
       CLI arguments
    """
    schema = _TransientListConfigSchema()

    return _create_transient_config_with_schema(cli_args, schema)


def create_transient_delete_config(cli_args: Dict[Any, Any]) -> Config:
    """Creates and validates the Config to be used by Transient-delete given
       the CLI arguments
    """
    schema = _TransientDeleteConfigSchema()

    return _create_transient_config_with_schema(cli_args, schema)


def create_transient_run_config(cli_args: Dict[Any, Any]) -> Config:
    """Creates and validates the Config to be used by Transient-run
       given the CLI arguments and, if specified, a config file

       Note that the CLI arguments take precedence over the config file
    """
    if cli_args["config"]:
        config = _consolidate_cli_args_and_config_file(cli_args)
    else:
        config = cli_args

    schema = _TransientRunConfigSchema()

    return _create_transient_config_with_schema(config, schema)
