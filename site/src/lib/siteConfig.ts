export const repositoryUrl = (
  import.meta.env.PUBLIC_REPOSITORY_URL ?? "https://github.com/yin-wen-bin/SAPBusinessAgents"
).replace(/\/+$/, "");

export const repositoryBranch = import.meta.env.PUBLIC_REPOSITORY_BRANCH ?? "main";
export const contributionGuideUrl = `${repositoryUrl}/blob/${repositoryBranch}/README.md`;
