/** 레벨과 교정 강도를 고치는 화면.
 *
 * 화면 자체는 껍데기고, 고르는 부분은 `settings/SettingsFold` 가 그린다.
 * 같은 부품을 대화 화면에도 끼워 넣을 수 있게 갈라 둔 것이다 — 대화 도중
 * 레벨을 바꾸려고 목록으로 나갔다 들어오면 그 대화가 끊긴다.
 *
 * 여기서도 **접힌 채로** 시작한다. 값 둘이 한눈에 보이고, 바꿀 하나만 펼치는 게
 * 폰에서는 선택지 여섯 줄을 늘어놓는 것보다 빠르다.
 */
import { Screen } from "../components/Screen";
import type { StrictnessOut } from "../api/types";
import { SettingsFold } from "./settings/SettingsFold";

import styles from "./SettingsScreen.module.css";

export interface SettingsScreenProps {
  /** `/strictness` 가 준 목록. App 이 한 번 받아서 넘겨준다. */
  strictness: StrictnessOut[];
  onBack: () => void;
}

export function SettingsScreen({ strictness, onBack }: SettingsScreenProps) {
  return (
    <Screen title="설정" onBack={onBack}>
      <p className={styles.lead}>지금 값이에요. 바꾸려면 줄을 눌러 주세요.</p>

      <SettingsFold strictness={strictness} />

      <p className={styles.note}>
        바꾼 값은 바로 저장되고, 앱을 껐다 켜도 그대로예요. 대화 중에 바꾸면 다음 대답부터
        적용돼요.
      </p>
    </Screen>
  );
}
