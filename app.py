from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

app = FastAPI()

# In-memory storage for items, containers, logs, and waste
items_db = {}
containers_db = {}
logs_db = []
waste_db = []

# Helper functions
def fits_in_container(item, container):
    # Check if an item fits in a container based on dimensions
    return (item["width"] <= container["width"] and
            item["depth"] <= container["depth"] and
            item["height"] <= container["height"])

def decrement_usage(item_id):
    # Decrement usage count for an item
    if item_id in items_db:
        items_db[item_id]["usageLimit"] -= 1
        if items_db[item_id]["usageLimit"] <= 0:
            waste_db.append(items_db.pop(item_id))

# API Endpoints
@app.post("/api/placement")
async def placement_recommendations(items: List[dict], containers: List[dict]):
    placements = []
    rearrangements = []

    for item in items:
        placed = False
        for container in containers:
            if fits_in_container(item, container):
                placements.append({
                    "itemId": item["itemId"],
                    "containerId": container["containerId"],
                    "position": {
                        "startCoordinates": {"width": 0, "depth": 0, "height": 0},
                        "endCoordinates": {
                            "width": item["width"],
                            "depth": item["depth"],
                            "height": item["height"]
                        }
                    }
                })
                placed = True
                break
        if not placed:
            rearrangements.append({"itemId": item["itemId"], "action": "rearrange"})
    
    return {"success": True, "placements": placements, "rearrangements": rearrangements}

@app.get("/api/search")
async def search_item(itemId: str = None, itemName: str = None):
    item = next((i for i in items_db.values() if i["itemId"] == itemId or i["name"] == itemName), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"success": True, "found": True, "item": item}

@app.post("/api/retrieve")
async def retrieve_item(request: dict):
    item_id = request.get("itemId")
    user_id = request.get("userId")
    timestamp = request.get("timestamp")

    if item_id not in items_db:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Log retrieval action
    logs_db.append({
        "timestamp": timestamp,
        "userId": user_id,
        "actionType": "retrieval",
        "itemId": item_id
    })

    # Decrement usage count
    decrement_usage(item_id)

    return {"success": True}

@app.post("/api/place")
async def place_item(request: dict):
    item_id = request.get("itemId")
    user_id = request.get("userId")
    timestamp = request.get("timestamp")
    container_id = request.get("containerId")
    position = request.get("position")

    if item_id not in items_db:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Update item's position
    items_db[item_id]["containerId"] = container_id
    items_db[item_id]["position"] = position

    # Log placement action
    logs_db.append({
        "timestamp": timestamp,
        "userId": user_id,
        "actionType": "placement",
        "itemId": item_id,
        "details": {"toContainer": container_id}
    })

    return {"success": True}

@app.get("/api/waste/identify")
async def identify_waste():
    waste_items = [
        {**item, "reason": "Expired" if item.get("expiryDate") < datetime.now().isoformat() else "Out of Uses"}
        for item in waste_db
    ]
    return {"success": True, "wasteItems": waste_items}

@app.post("/api/waste/return-plan")
async def waste_return_plan(request: dict):
    undocking_container_id = request.get("undockingContainerId")
    undocking_date = request.get("undockingDate")
    max_weight = request.get("maxWeight")

    total_weight = 0
    return_plan = []
    retrieval_steps = []

    for item in waste_db:
        if total_weight + item["mass"] > max_weight:
            break
        return_plan.append({
            "step": len(return_plan) + 1,
            "itemId": item["itemId"],
            "itemName": item["name"],
            "fromContainer": item.get("containerId"),
            "toContainer": undocking_container_id
        })
        total_weight += item["mass"]

    return {
        "success": True,
        "returnPlan": return_plan,
        "retrievalSteps": retrieval_steps,
        "returnManifest": {
            "undockingContainerId": undocking_container_id,
            "undockingDate": undocking_date,
            "returnItems": return_plan,
            "totalVolume": sum(item["width"] * item["depth"] * item["height"] for item in waste_db),
            "totalWeight": total_weight
        }
    }

@app.post("/api/simulate/day")
async def simulate_time(request: dict):
    num_days = request.get("numOfDays", 1)
    items_used = []
    items_expired = []
    items_depleted = []

    for _ in range(num_days):
        for item in list(items_db.values()):
            if item.get("expiryDate") and datetime.fromisoformat(item["expiryDate"]) < datetime.now():
                items_expired.append(item)
                waste_db.append(item)
                del items_db[item["itemId"]]
            elif item["usageLimit"] <= 0:
                items_depleted.append(item)
                waste_db.append(item)
                del items_db[item["itemId"]]
            else:
                item["usageLimit"] -= 1
                items_used.append({"itemId": item["itemId"], "remainingUses": item["usageLimit"]})

    return {
        "success": True,
        "newDate": (datetime.now() + timedelta(days=num_days)).isoformat(),
        "changes": {
            "itemsUsed": items_used,
            "itemsExpired": items_expired,
            "itemsDepletedToday": items_depleted
        }
    }

@app.post("/api/import/items")
async def import_items(file: UploadFile = File(...)):
    try:
        df = pd.read_csv(file.file)
        for _, row in df.iterrows():
            items_db[row["itemId"]] = {
                "itemId": row["itemId"],
                "name": row["name"],
                "width": row["width_cm"],
                "depth": row["depth_cm"],
                "height": row["height_cm"],
                "mass": row["mass_kg"],
                "priority": row["priority"],
                "expiryDate": row["expiry_date"],
                "usageLimit": row["usage_limit"],
                "preferredZone": row["preferred_zone"]
            }
        return {"success": True, "itemsImported": len(items_db)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/import/containers")
async def import_containers(file: UploadFile = File(...)):
    try:
        df = pd.read_csv(file.file)
        for _, row in df.iterrows():
            containers_db[row["containerId"]] = {
                "containerId": row["containerId"],
                "zone": row["zone"],
                "width": row["width_cm"],
                "depth": row["depth_cm"],
                "height": row["height_cm"]
            }
        return {"success": True, "containersImported": len(containers_db)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/logs")
async def get_logs(startDate: str = None, endDate: str = None, itemId: str = None, userId: str = None, actionType: str = None):
    filtered_logs = logs_db
    if startDate:
        filtered_logs = [log for log in filtered_logs if log["timestamp"] >= startDate]
    if endDate:
        filtered_logs = [log for log in filtered_logs if log["timestamp"] <= endDate]
    if itemId:
        filtered_logs = [log for log in filtered_logs if log["itemId"] == itemId]
    if userId:
        filtered_logs = [log for log in filtered_logs if log["userId"] == userId]
    if actionType:
        filtered_logs = [log for log in filtered_logs if log["actionType"] == actionType]
    return {"logs": filtered_logs}
