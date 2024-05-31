#!/bin/bash

# SPDX-FileCopyrightText: NVIDIA CORPORATION & AFFILIATES
# Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

# Runs the isaac_ros_data_recorder with a specified yaml file, then summarizes the outputs automatically
# Meant to be run inside of the dev container.

set -m

RECORDING_DIR="/mnt/nova_ssd/recordings"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

CONFIG_DIR="$(ros2 pkg prefix isaac_ros_data_recorder)/share/isaac_ros_data_recorder/config"

# Default paths
app_config="nova-carter_hawk-4.yaml"
bag_file_base="rosbag2"
override_name=false
skip_validation=false

show_help() {
    echo "Usage: $0 [options]"
    echo
    echo "Options:"
    echo "  -h                   Display this help message."
    echo "  -y | --yaml PATH     Path to the YAML spec file. If not provided, $app_config is used."
    echo "  -o | --output STRING String to prefix the output bag file. If not provided, defaults to '$bag_file_base'."
    echo "  --override_name       When present, will not prepend RECORDING_DIR or append datetime to --output."
    echo "  --skip_validation    Skip the validation process."
    echo
    echo "Example:"
    echo "  $0 -y /path/to/yaml_file.yaml -o unique_name"
    echo "  $0 --yaml /path/to/yaml_file.yaml --output unique_name --override_name"
    echo "  $0 -h"
    echo
}

handle_sigint() {
    # Forward the SIGINT to the last process
    echo "Signint caught, stopping recorder"
    kill -SIGINT "$ros2_launch_pid"
    wait "$ros2_launch_pid"
}

generate_git_summary() {
    local output_file=$1
    local separator="----------------------------------------------"

    error_handler() {
        echo "Git info not available" > "$output_file"
        exit 1
    }
    trap 'error_handler' ERR

    # For now, just cd into the script dir, we will probabaly want to add summaries for
    # all relavent repos eventually
    cd "$SCRIPT_DIR"
    # Check if we're in a Git repository
    if ! git -C "$git_repo_dir" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
        error_handler
    fi

    echo "$separator" >> "$output_file"
    echo "Status" >> "$output_file"
    echo "$separator" >> "$output_file"
    git status >> "$output_file" || error_handler

    # Write the recent commit history to the file
    echo "$separator" >> "$output_file"
    echo "Recent commit history:" >> "$output_file"
    echo "$separator" >> "$output_file"
    git log --oneline -5 >> "$output_file" || error_handler
    echo >> "$output_file"

    # Check the Git status and append to the file
    status=$(git status --porcelain) || error_handler
    if [ -z "$status" ]; then
        echo "$separator" >> "$output_file"
        echo "No uncommitted changes." >> "$output_file"
    else
        echo "$separator" >> "$output_file"
        echo "Uncommitted changes:" >> "$output_file"
        echo "$separator" >> "$output_file"
        git status >> "$output_file" || error_handler
        echo $separator >> "$output_file"
        echo "Diff of uncommitted changes:" >> "$output_file"
        echo "$separator" >> "$output_file"
        git diff >> "$output_file" || error_handler
    fi

    echo "$separator" >> "$output_file"
}

trap handle_sigint SIGINT

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -y|--yaml)
            app_config="$2"
            shift # past argument
            shift # past value
            ;;
        -o|--output)
            bag_file_base="$2"
            shift # past argument
            shift # past value
            ;;
        --override_name)
            override_name=true
            shift # past argument
            ;;
         --skip_validation)
            skip_validation=true
            shift # past argument
            ;;
        *)
            # Unknown option
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Process bag_file_base
if [ "$override_name" = false ]; then
    current_date_time=$(date +"%Y_%m_%d-%H_%M_%S")
    rosbag_name="${bag_file_base}_${current_date_time}"
else
    rosbag_name="${bag_file_base}"
fi

output_dir="${RECORDING_DIR}/${rosbag_name}"

# process app config yaml
if [[ -f "$app_config" ]]; then
    app_config="$app_config"
else
    # If it's not a file, prepend the config directory
    app_config="$CONFIG_DIR/$app_config"

    # Check if the file exists in the config directory
    if [[ ! -f "$app_config" ]]; then
        echo "Error: $app_config does not exist"
        echo "Available options:"
        ls "$CONFIG_DIR"
        echo "Or provide an absolute path to a valid yaml file"
        exit 1
    fi
fi

if ! python -c "import isaac_ros_data_validation" &> /dev/null; then
    echo "isaac_ros_data_validation is not installed: https://gitlab-master.nvidia.com/isaac_ros/isaac_ros_nova Exiting."
    exit 1
fi

config_arg="config:=${app_config}"
rosbag_arg="rosbag_name:=${rosbag_name}"
datetime_arg="append_datetime:=False"
recording_dir_arg="recording_dir:=${RECORDING_DIR}"
ros2_launch_command="ros2 launch isaac_ros_data_recorder data_recorder.launch.py $config_arg $rosbag_arg $recording_dir_arg $datetime_arg"
echo $ros2_launch_command

# Run the data recorder and grab the output
$ros2_launch_command &
ros2_launch_pid=$!

wait

ros2 bag info $output_dir

if [ "$skip_validation" = false ]; then
    SECONDS=0
    bag_check_command="python -m isaac_ros_data_validation.summarize_bag $output_dir"
    echo $bag_check_command
    eval $bag_check_command | tee "${output_dir}/data_validation.txt"
    bag_check_duration=$SECONDS
    bag_check_minutes=$((bag_check_duration / 60))
    bag_check_seconds=$((bag_check_duration % 60))
fi

# Check the time and size the output directory
bag_check_time="${bag_check_minutes}m ${bag_check_seconds}s"

echo "Data validation took $bag_check_time."
echo "Bag was saved in $output_dir"

# Now add some extra info to the bag file
generate_git_summary "${output_dir}/git_summary.txt"
