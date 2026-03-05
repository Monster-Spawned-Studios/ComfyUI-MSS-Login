"""
Copyright © 2026 Monster Spawned Studios
https://monsterspawned.studio/
All Rights Reserved.
"""

import logging
import subprocess
from argparse import ArgumentParser
from os import getcwd, makedirs
from os.path import exists
from platform import machine, system
from time import sleep

parser = ArgumentParser(
    description="Setup the local development environment for the ComfyUI-MSS-Login extension."
)
parser.add_argument(
    "-p","--python",
    type=str,
    default="3.13",
    help="The version of Python to use for the virtual environment.",
)
parser.add_argument(
    "-g","--dependency-groups",
    type=str,
    default=["dev"],
    help="The groups to use for the development dependencies.",
    choices=["dev", "comfyui", "documentation", "production"],
)
parser.add_argument("-D","--debug", action="store_true", help="Enable debug logging.")
parser.add_argument(
    "-i","--install-comfyui",
    action="store_true",
    help="Install ComfyUI  and its dependencies.",
)
parser.add_argument(
    "-d","--comfyui-dir",
    type=str,
    default="ComfyUI",
    help="The directory to install ComfyUI to.",
)
parser.add_argument(
    "-m","--m-series",
    action="store_true",
    help="Install ComfyUI and its dependencies for Apple M-Series.",
)
parser.add_argument(
    "-nv","--cuda",
    action="store_true",
    help="Install ComfyUI and its dependencies for NVIDIA CUDA GPUs.",
)
parser.add_argument(
    "-r","--rocm",
    action="store_true",
    help="Install ComfyUI and its dependencies for AMD ROCm GPUs.",
)
parser.add_argument(
    "-c","--cpu",
    action="store_true",
    help="Install ComfyUI and its dependencies for use with a CPU only.",
)
args = parser.parse_args()

logger = logging.getLogger("MSS-Login Setup Tool")

logger.info("The application has started and will now begin its task.")

arg_values = {
    "python_version": args.python,
    "dependency_groups": args.dependency_groups,
    "debug_mode": args.debug,
    "install_comfyui": args.install_comfyui,
    "comfyui_dir": args.comfyui_dir,
    "m_series": args.m_series,
    "cuda": args.cuda,
    "rocm": args.rocm,
    "cpu": args.cpu,
}

log_file = f"{getcwd()}/logs/setup_dev.log"
if not exists(f"{getcwd()}/logs"):
    try:
        makedirs(f"{getcwd()}/logs", exist_ok=True)
    except Exception as e:
        logger.error("Failed to create the logs directory: %s", e)
        logger.error("Please create the logs directory manually and try again.")
        logger.error("The logs directory should be located at: %s", f"{getcwd()}/logs")
        logger.error("This application will now exit.")
        sleep(3)
    exit(1)

if arg_values["debug_mode"]:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename=log_file,
        filemode="a",
    )
    logger.debug("Debug logging enabled.")
else:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename=log_file,
        filemode="a",
    )


def setup_dev(
    python_version: str = "3.13", dev_group: str = "dev", comfyui_group: str = "comfyui"
) -> bool:
    """
    Setup the local development environment for the ComfyUI-MSS-Login extension.

    This function will:
    - Create a virtual environment for the project, using the `uv venv` command.
    - Install the dependencies for the project, using the `uv sync` command.
    - Create a local ComfyUI installation, using the `comfy` command (based on
            the current system platform and architecture).

    Args:
        python_version: The version of Python to use for the virtual environment.
        dev_group: The group to use for the development dependencies.
        comfyui_group: The group to use for the ComfyUI dependencies.
    """
    logger.info(
        "[MSS-Login] Setting up the local development environment for the ComfyUI-MSS-Login extension using Python %s.",
        python_version,
    )
    logger.debug(
        "Creating a virtual environment for the project using Python %s.",
        python_version,
    )
    try:
        subprocess.run(["uv", "venv", "--python", f"{python_version}"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to create a virtual environment for the project: %s", e)
        return False
    logger.debug(
        "Installing the dependencies for the project using the %s group.",
        dev_group,
    )
    try:
        subprocess.run(["uv", "sync", "--group", f"{dev_group}"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to install the dependencies for the project: %s", e)
        return False
    logger.debug(
        "Creating a local ComfyUI installation using the %s group.",
        comfyui_group,
    )
    try:
        subprocess.run(["comfy", "install", "--group", comfyui_group], check=True)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to create a local ComfyUI installation: %s", e)
        return False
    logger.info(
        "[MSS-Login] Local development environment setup complete for the ComfyUI-MSS-Login extension using Python %s.",
        python_version,
    )
    return True


def install_comfyui(
    python_version: str = "3.13",
    comfyui_group: str = "comfyui",
    comfyui_dir: str = "ComfyUI",
) -> bool:
    """Install ComfyUI and its dependencies.

    Args:
        python_version: The version of Python to use for the virtual environment.
        dev_group: The group to use for the development dependencies.
        comfyui_group: The group to use for the ComfyUI dependencies.
        debug_mode: Whether to enable debug logging.
        verbose: Whether to enable verbose logging.
        install_comfyui: Whether to install ComfyUI and its dependencies.
    """
    logger.info(
        "Installing ComfyUI and its dependencies using Python version '%s'.",
        python_version,
    )
    logger.debug(
        "Installing ComfyUI and its dependencies using the %s group into the %s directory.",
        comfyui_group,
        comfyui_dir,
    )
    try:
        if (system() == "Darwin" and machine() == "arm64") or arg_values.get(
            "m_series", False
        ):  # Apple M-Series
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    comfyui_group,
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--m-series",
                    "--fast-deps",
                ],
                check=True,
            )
        elif (
            (system() == "Linux" and machine() == "x86_64")
            or (system() == "Windows" and machine() == "x86_64")
            or arg_values.get("cuda", False)
        ):  # Linux x86_64 or Windows x86_64 or NVIDIA CUDA
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    comfyui_group,
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--nvidia",
                    "--fast-deps",
                ],
                check=True,
            )
        elif (system() == "Linux" and machine() == "aarch64") or arg_values.get(
            "rocm", False
        ):  # Linux aarch64 or AMD ROCm
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    comfyui_group,
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--amd",
                    "--fast-deps",
                ],
                check=True,
            )
        elif arg_values.get("cpu", False):  # CPU only
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--group",
                    f"{comfyui_group}",
                    "comfy",
                    f"--workspace={comfyui_dir.replace('\\', '/')}",
                    "install",
                    "--cpu",
                    "--fast-deps",
                ],
                check=True,
            )
        else:
            logger.error("No supported platform found for ComfyUI installation.")
            return False
    except subprocess.CalledProcessError as e:
        logger.error(
            "Failed to install ComfyUI and its dependencies into the %s directory: %s",
            comfyui_dir,
            e,
        )
        return False
    logger.info(
        "[MSS-Login] ComfyUI and its dependencies installed successfully into the %s directory using Python version '%s'.",
        comfyui_dir,
        python_version,
    )
    return True


if __name__ == "__main__":
    """
    Main function to setup the local development environment for the ComfyUI-MSS-Login extension.

    Args:
        python_version: The version of Python to use for the virtual environment.
        dev_group: The group to use for the development dependencies.
        comfyui_group: The group to use for the ComfyUI dependencies.
        debug_mode: Whether to enable debug logging.
        verbose: Whether to enable verbose logging.
        install_comfyui: Whether to install ComfyUI and its dependencies.
    """
    try:
        try:
            result = subprocess.run(["uv", "--help"], check=True, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error("Failed to check the uv version: %s", result.stderr)
                sleep(3)
                exit(1)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to check the uv version: %s\nPlease install uv manually using the `pip install -U uv` command.", e)
            sleep(3)
            exit(1)
        if setup_dev(
            arg_values["python_version"],
            arg_values["dependency_groups"],
        ):
            logger.info("Local development environment setup complete.")
            if arg_values.get("install_comfyui", False):
                if install_comfyui(
                    arg_values["python_version"],
                    arg_values["dependency_groups"],
                    arg_values["comfyui_dir"],
                ):
                    logger.info("ComfyUI and its dependencies installed successfully.")
                else:
                    logger.error("Failed to install ComfyUI and its dependencies.")
            else:
                logger.info(
                    "[MSS-Login] ComfyUI and its dependencies not installed. You can install them manually using the `install_comfyui` flag."
                )
    except Exception as e:
        logger.error("Failed to setup the local development environment: %s", e)
        logger.info("Please review the log file for more information.")
        logger.info("The log file is located at: %s", log_file)
    finally:
        logger.info("The application has completed its task and will now exit.")
        sleep(3)
        exit(0)
