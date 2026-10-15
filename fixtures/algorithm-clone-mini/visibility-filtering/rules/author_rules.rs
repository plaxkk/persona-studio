// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 visibility-filtering/rules/author_rules.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。

// 只在推荐位生效的那一类：可以留在关注流里，但进不了推荐位。
pub(super) const RULE_RECOMMENDATIONS_ONLY_DROP: Rule = Rule {
    id: RuleId::FIXTURE_RECOMMENDATIONS_ONLY_DROP,
    audience: NonAuthors,
    when: any![AuthorHasLabel(FIXTURE_DO_NOT_AMPLIFY_LABEL)],
    action: RuleAction::spec(ActionSpec::Drop),
};
