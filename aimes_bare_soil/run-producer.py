#!/usr/bin/python
# -*- coding: UTF-8

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/. */

# Authors:
# Michael Berg-Mohnicke <michael.berg@zalf.de>
#
# Maintainers:
# Currently maintained by the authors.
#
# This file has been created at the Institute of
# Landscape Systems Analysis at the ZALF.
# Copyright (C: Leibniz Centre for Agricultural Landscape Research (ZALF)

import capnp
from collections import defaultdict
from datetime import date, timedelta, datetime
import json
import os
import pandas
from pathlib import Path
import sys
import time
import zmq

import monica_run_lib

from zalfmas_common import common
from zalfmas_common.model import monica_io
import zalfmas_capnpschemas

sys.path.append(os.path.dirname(zalfmas_capnpschemas.__file__))
import fbp_capnp

PATHS = {
    # adjust the local path to your environment
    "mbm-local-local": {
        "monica-path-to-climate-dir": "/home/berg/GitHub/amei_monica_soil_temperature_sensitivity_analysis/input_data/WeatherData/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
    "mbm-win-local-local": {
        "monica-path-to-climate-dir": "C:/Users/berg/GitHub/amei_monica_soil_temperature_sensitivity_analysis/input_data/WeatherData/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
    "mbm-local-remote": {
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
    "hpc-local-remote": {
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        # mounted path to archive accessable by monica executable
        "path-to-data-dir": "./data/",  # mounted path to archive or hard drive with data
        "path-debug-write-folder": "./debug-out/",
    },
}


def run_producer(server=None, port=None):
    context = zmq.Context()
    socket = context.socket(zmq.PUSH)  # pylint: disable=no-member

    config = {
        "mode": "mbm-win-local-local",
        "server-port": port if port else "6666",
        "server": server if server else "localhost",  # "login01.cluster.zalf.de",
        "sim.json": "sim.json",
        "crop.json": "crop.json",
        "site.json": "site.json",
    }

    common.update_config(config, sys.argv, print_config=True, allow_new_keys=False)

    # select paths
    paths = PATHS[config["mode"]]
    # connect to monica proxy (if local, it will try to connect to a locally started monica)
    socket.connect("tcp://" + config["server"] + ":" + str(config["server-port"]))

    # read data from excel
    dfs = pandas.read_excel("AMEI_fallow_Aimes_2024-05-16.xlsx",
                            sheet_name=[
                                "Residue",
                                "initial_condition_layers",
                                "Planting_events",
                                "Harvest_events",
                                "Soil_metadata",
                                "Soil_profile_layers",
                                "Weather_stations",
                                "Weather_daily",
                            ],
                            header=2,
                            )

    #soil_data_csv = monica_run_lib.read_csv("input_data/SoilData.csv",
    #                                        key=("SOIL_ID", "SLID"), key_type=(str, int),
    #                                        header_row_line=3, data_row_start=4)

    #soil_metadata_csv = monica_run_lib.read_csv("input_data/SoilMetadata.csv",
    #                                        key="SOIL_ID", key_type=(str,),
    #                                        header_row_line=3, data_row_start=4)


    #treatment_csv = monica_run_lib.read_csv("input_data/Treatment.csv",
    #                                        key="SM", key_type=(str,),
    #                                        header_row_line=3, data_row_start=4)

    #weather_metadata_csv = monica_run_lib.read_csv("input_data/WeatherMetadata.csv",
    #                                               key="WST_ID", key_type=(str,),
    #                                               header_row_line=3, data_row_start=4)

    soil_profiles_dict = defaultdict(dict)
    sps = dfs["Soil_profile_layers"]
    for i in sps.axes[0]:
        soil_profiles_dict[sps["SOIL_ID"]][i] = {
            "Thickness": [(float(sps["SLLB"]) - float(sps["SLLT"])) / 100, "m"],
            "SoilOrganicCarbon": [float(sps["SLOC"]), "% (g[C]/100g[soil])"],
            "SoilBulkDensity": [float(sps["SLBDM"]) * 1000, "kg m-3"],
            "FieldCapacity": [float(sps["SLDUL"]), "m3/m3"],
            "PoreVolume": [float(sps["SLSAT"]), "m3/m3"],
            "PermanentWiltingPoint": [float(sps["SLLL"]), "m3/m3"],
            "Clay": [float(sps["SLCLY"]), "%"],
            "Sand": [float(sps["SLSND"]), "%"],
            "PH": [float(sps["SLPHW"]), ""],
            "CN": [float(sps["SLCN"]), ""],
            #"Lambda": [float(sps["SLDRL"]), ""],
        }
    soil_profiles = defaultdict(list)
    for soil_id, layers_dict in soil_profiles_dict.items():
        for lid in sorted(layers_dict.keys()):
            soil_profiles[soil_id].append(layers_dict[lid])

    # read template sim.json
    with open(config["sim.json"]) as _:
        sim_json = json.load(_)
    # read template site.json
    with open(config["site.json"]) as _:
        site_json = json.load(_)
    # read template crop.json
    with open(config["crop.json"]) as _:
        crop_json = json.load(_)
    # create environment template from json templates
    env_template = monica_io.create_env_json_from_json_config({
        "crop": crop_json,
        "site": site_json,
        "sim": sim_json,
        "climate": ""
    })

    sent_env_count = 0
    start_time = time.perf_counter()
    for treatment_id, t_data in treatment_csv.items():
        start_setup_time = time.perf_counter()

        soil_id = t_data["SOIL_ID"]
        wst_id = t_data["WST_ID"]
        soil_profile = soil_profiles[soil_id]
        env_template["params"]["siteParameters"]["SoilProfileParameters"] = soil_profile
        env_template["params"]["siteParameters"]["Latitude"] = float(weather_metadata_csv[wst_id]["XLAT"])
        env_template["csvViaHeaderOptions"] = sim_json["climate.csv-options"]
        env_template["pathToClimateCSV"] = f"{paths['monica-path-to-climate-dir']}/{t_data['WST_DATASET']}.WTH"
        # print("pathToClimateCSV:", env_template["pathToClimateCSV"])

        awc = float(t_data["AWC"])
        env_template["params"]["userSoilTemperatureParameters"]["PlantAvailableWaterContentConst"] = awc

        env_template["params"]["simulationParameters"]["customData"] = {
            "LAI": float(t_data["LAID"]),
            "AWC": awc,
            "CWAD": float(t_data["CWAD"]),
            "IRVAL": float(t_data["IRVAL"]),
            "MLTHK": float(t_data["MLTHK"]),
            "SALB": float(soil_metadata_csv[soil_id]["SALB"]),
            "SLDP": float(soil_metadata_csv[soil_id]["SLDP"]),
            "SABDM": float(soil_metadata_csv[soil_id]["SABDM"]),
            "XLAT": float(weather_metadata_csv[wst_id]["XLAT"]),
            "XLONG": float(weather_metadata_csv[wst_id]["XLONG"]),
            "TAMP": float(weather_metadata_csv[wst_id]["TAMP"]),
            "TAV": float(weather_metadata_csv[wst_id]["TAV"]),
        }

        env_template["customId"] = {
            "env_id": sent_env_count + 1,
            "location": wst_id,
            "soil": soil_id,
            "lai": f"L{t_data['LAID']}",
            "aw": f"AW{t_data['AWC']}",
            
            "layerThickness": site_json["SiteParameters"]["LayerThickness"][0],
            "profileLTs": list(map(lambda layer: layer["Thickness"][0], soil_profile))
        }

        socket.send_json(env_template)
        sent_env_count += 1

        stop_setup_time = time.perf_counter()
        print("Setup ", sent_env_count, " envs took ", (stop_setup_time - start_setup_time), " seconds")

    env_template["customId"] = {
        "no_of_sent_envs": sent_env_count,
    }
    socket.send_json(env_template)

    stop_time = time.perf_counter()

    # write summary of used json files
    try:
        print("sending ", (sent_env_count - 1), " envs took ", (stop_time - start_time), " seconds")
        print("exiting run_producer()")
    except Exception:
        raise


if __name__ == "__main__":
    run_producer()