import os
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from cortex.core.config import DEFAULT_DATASET, DEFAULT_OLLAMA_HOST
from cortex.core.database import (
    save_benchmark_run, update_benchmark_run_status, get_benchmark_runs,
    get_benchmark_run, delete_benchmark_run
)
from cortex.core.benchmark import manager as benchmark_manager

router = APIRouter(prefix="/api", tags=["Benchmarks"])

class BenchmarkRunRequest(BaseModel):
    dataset: str = "benchmark_dataset.json"
    judge_model: str = "qwen3.5:9b"
    reuse_cache: bool = False
    comment: str = None

@router.get("/benchmarks")
async def api_get_benchmarks():
    try:
        runs = get_benchmark_runs()
        return {"runs": runs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.get("/benchmarks/status")
async def api_benchmark_status():
    return benchmark_manager.get_status()

@router.get("/benchmarks/{run_id}")
async def api_get_benchmark(run_id: int):
    try:
        run = get_benchmark_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Benchmark run not found.")
        return run
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/benchmarks/run")
async def api_run_benchmark(req: BenchmarkRunRequest):
    # Check if a benchmark is already running
    status = benchmark_manager.get_status()
    if status["status"] == "running":
        raise HTTPException(status_code=400, detail="A benchmark run is already in progress.")
        
    dataset_path = req.dataset
    if not os.path.exists(dataset_path):
        # Locate it in workspace base
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        dataset_path = os.path.join(base_dir, req.dataset)
        if not os.path.exists(dataset_path):
            dataset_path = DEFAULT_DATASET
            
    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            dataset = json.load(f)
        total_q = len(dataset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load dataset: {str(e)}")
        
    dataset_name = os.path.basename(dataset_path)
    
    # Save a run entry as 'running'
    try:
        run_id = save_benchmark_run(dataset_name, req.judge_model, total_q, req.comment)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save benchmark run to DB: {str(e)}")
        
    # Start the benchmark run asynchronously in background thread
    try:
        ollama_host = DEFAULT_OLLAMA_HOST
        
        benchmark_manager.start(
            run_id=run_id,
            dataset_path=dataset_path,
            judge_model=req.judge_model,
            api_url="http://127.0.0.1:8000",
            ollama_host=ollama_host,
            reuse_cache=req.reuse_cache,
            delay=1.0,
            timeout=150.0
        )
        return {"message": "Benchmark started successfully.", "run_id": run_id}
    except Exception as e:
        # Mark run as failed
        update_benchmark_run_status(run_id, "failed")
        raise HTTPException(status_code=500, detail=f"Failed to start benchmark: {str(e)}")

@router.post("/benchmarks/stop")
async def api_stop_benchmark():
    stopped = benchmark_manager.stop()
    if stopped:
        return {"message": "Benchmark cancellation signal sent successfully."}
    return {"message": "No active benchmark run found to cancel."}

@router.delete("/benchmarks/{run_id}")
async def api_delete_benchmark(run_id: int):
    try:
        success = delete_benchmark_run(run_id)
        if not success:
            raise HTTPException(status_code=404, detail="Benchmark run not found.")
        return {"message": f"Benchmark run {run_id} deleted successfully."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
