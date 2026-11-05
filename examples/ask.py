from logintel import Agent

agent = Agent()
for q in [
    # "What caused the 503 errors for TENANT-X around 12:02-12:03?",
    "Were there any pool connection warnings for TENANT-X between 12:02:00 and 12:02:56?",
]:
    a = agent.ask(q)                       # or agent.ask(q, use_case="incident")
    print(a.path, a.confidence, a.answer, *a.citations, sep="\n")