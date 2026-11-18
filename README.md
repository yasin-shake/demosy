# demos

Reference apps built with Streamlit. Each folder is a self-contained app with
its own `requirements.txt`. All data is synthetic.

| App | Use case | Live demo |
| --- | --- | --- |
| [`bi-dashboard`](bi-dashboard) | Dashboards: Nimbus BI, a revenue dashboard with layout-first loading, filters in the URL, certified metrics, drill-down drawers, and an Ask AI drawer that answers from the same data | [advanced-dashboard-demo.streamlit.app](https://advanced-dashboard-demo.streamlit.app/) |
| [`forecasting-app`](forecasting-app) | Forecasting apps: Cairn Demand lab, a Monte Carlo demand and pricing simulator with what-if levers | [forecasting-app-demo.streamlit.app](https://forecasting-app-demo.streamlit.app/) |
| [`workflow-app`](workflow-app) | Workflow apps: Cairn Reorder desk, a review queue where planners approve the purchase orders an AI agent proposes | [workflow-app-demo.streamlit.app](https://workflow-app-demo.streamlit.app/) |
| [`agent-app`](agent-app) | AI chat and agent apps: Cairn Analyst, a chat-based analyst that shows its steps, streams its answers, opens its analysis in a canvas, and drafts orders people approve | [agent-app-demo.streamlit.app](https://agent-app-demo.streamlit.app/) |

The apps run on Streamlit Community Cloud, so one that has been idle may take a
moment to wake up.

## Running locally

Each app is self-contained: install its own `requirements.txt` from its own
folder and run it from there.

```bash
cd bi-dashboard   # or forecasting-app, workflow-app, agent-app
pip install -r requirements.txt
streamlit run streamlit_app.py
```

`requirements.txt` pins a preview build of Streamlit (needed for features like
`st.skeleton` and `bind="query-params"`) straight from its wheel URL; a plain
`pip install -r requirements.txt` installs it like any other dependency.
Switch to a stable release once one ships these features.

Each app also has its own test suite:

```bash
pytest tests
```

See each app's own README for what it demonstrates and for driving it
programmatically over its agent API.

### Running all four at once

The apps don't share ports, so you can run them side by side on their own:

```bash
(cd bi-dashboard && streamlit run streamlit_app.py --server.port 8501) &
(cd forecasting-app && streamlit run streamlit_app.py --server.port 8502) &
(cd workflow-app && streamlit run streamlit_app.py --server.port 8503) &
(cd agent-app && streamlit run streamlit_app.py --server.port 8504) &
```

### For AI coding agents

If you're an agent (or using one) to develop these apps further, install
Streamlit's bundled reference docs once, matched to the preview build these
apps pin:

```bash
pip install -r bi-dashboard/requirements.txt  # or any app's requirements.txt
streamlit skills --yes
```

This adds a `developing-with-streamlit` skill with reference docs for theming,
dashboards, charts, performance, and the agent API — all matched to the
installed Streamlit version rather than a model's training data.
