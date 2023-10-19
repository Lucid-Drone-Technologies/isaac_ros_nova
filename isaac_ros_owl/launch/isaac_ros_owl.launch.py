# SPDX-FileCopyrightText: NVIDIA CORPORATION & AFFILIATES
# Copyright (c) 2021-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import launch
from launch_ros.actions import ComposableNodeContainer
from launch_ros.descriptions import ComposableNode


def generate_launch_description():
    correlated_timestamp_driver_node = ComposableNode(
        package='isaac_ros_correlated_timestamp_driver',
        plugin='nvidia::isaac_ros::correlated_timestamp_driver::CorrelatedTimestampDriverNode',
        name='correlated_timestamp_driver',
        parameters=[{'use_time_since_epoch': False,
                     'nvpps_dev_name': '/dev/nvpps0'}])

    owl_node = ComposableNode(
        name='owl_node',
        package='isaac_ros_owl',
        plugin='nvidia::isaac_ros::owl::OwlNode',
        namespace='',
    )

    owl_container = ComposableNodeContainer(
            name='owl_container',
            package='rclcpp_components',
            executable='component_container_mt',
            composable_node_descriptions=[owl_node,
                                          correlated_timestamp_driver_node],
            namespace='',
            output='screen',
            arguments=['--ros-args', '--log-level', 'info'],
        )

    return launch.LaunchDescription([owl_container])
