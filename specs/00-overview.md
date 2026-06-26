# SPEC 00 — Project Overview

## Purpose

Build a real-time, browser-based AI simulation world where a **local LLM acts as the brain for each inhabitant**. The goal is to observe how an LLM can evolve and manage a small society through autonomous agents that coexist like humans.

## Core Idea

- A top-down pixel-art world is rendered in the browser.
- A roster of agents lives in the world (8 active by default, up to 12 via the `?agents=N` override), each with its own role, memory, relationships, and resources.
- Each agent's decisions come from a local LLM served by LM Studio.
- Agents act independently and in real time — moving, talking, trading, collecting resources, contributing to shared build projects, proposing new structures, and changing roles over time.

## Reference: Project Sid

This project is inspired by the "Project Sid" research paper (many-agent simulations toward AI civilization). The concepts borrowed:

| Project Sid concept | How we implement it (simplified) |
|---------------------|----------------------------------|
| Role specialization | Agents start with a role and can change it based on experience |
| Social awareness | Each agent tracks relationships (ally / neutral / rival) |
| Memory | Each agent remembers its last 5 actions |
| Collective behavior | Agents trade resources and form bonds over time |

We deliberately keep this **far simpler** than Project Sid — no constitutions, no voting, no religion, no 500-agent scale. Just a handful of agents proving the core loop works, with a lightweight build/blueprint pipeline so the village can visibly grow.

## What "behaving like a human" means here

An agent should make decisions that a real person in that role might make: a farmer tends the farm and trades surplus food; a trader brokers exchanges; a guard patrols; an elder gives advice and moves slowly. The LLM drives these choices; the code enforces the outcomes.

## Non-Goals

This is a proof-of-concept of the LLM-as-brain loop. It is not a game, not a product, and not a research-grade simulation. Keep everything minimal and observable.
