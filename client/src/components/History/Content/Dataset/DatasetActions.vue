<script setup lang="ts">
import axios from "axios";

import { faBug, faChartBar, faInfoCircle, faLink, faRedo, faSitemap, faKey} from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/vue-fontawesome";
import { BButton } from "bootstrap-vue";
import { computed } from "vue";
import { useRouter } from "vue-router/composables";

import type { HDADetailed } from "@/api";
import { copy as sendToClipboard } from "@/utils/clipboard";
import localize from "@/utils/localization";
import { absPath, prependPath } from "@/utils/redirect";

import type { ItemUrls } from ".";

import { useHistoryStore } from "@/stores/historyStore";
import {setAttributes} from "@/components/DatasetInformation/services";
import DatasetDownload from "@/components/History/Content/Dataset/DatasetDownload.vue";
import { getAppRoot } from "@/onload/loadConfig";

interface Props {
    item: HDADetailed;
    writable: boolean;
    showHighlight: boolean;
    itemUrls: ItemUrls;
}

const props = withDefaults(defineProps<Props>(), {
    writable: true,
    showHighlight: false,
});

const emit = defineEmits(["toggleHighlights"]);

const router = useRouter();
const historyStore = useHistoryStore();

const showDownloads = computed(() => {
    return !props.item.purged && ["ok", "failed_metadata", "error"].includes(props.item.state);
});
const showError = computed(() => {
    return props.item.state === "error" || props.item.state === "failed_metadata";
});
const showInfo = computed(() => {
    return props.item.accessible;
});
const showVisualizations = computed(() => {
    return !props.item.purged && ["ok", "failed_metadata", "error"].includes(props.item.state);
});

const showRecrypt = computed(() => {
    return (
        (props.item.extension == "c4gh" || props.item.extension.endsWith(".c4gh")) &&
        props.item.state != "error" &&
        props.item.state != "failed_metadata" &&
        props.item.state != "upload" &&
        props.item.state != "noPermission"
    );
});
const showRerun = computed(() => {
    return props.item.accessible && props.item.rerunnable && props.item.creating_job && props.item.state != "upload";
});
const reportErrorUrl = computed(() => {
    return prependPath(props.itemUrls.reportError!);
});
const showDetailsUrl = computed(() => {
    return prependPath(props.itemUrls.showDetails!);
});
const visualizeUrl = computed(() => {
    return prependPath(props.itemUrls.visualize!);
});
const rerunUrl = computed(() => {
    return prependPath(props.itemUrls.rerun!);
});
const downloadUrl = computed(() => {
    return prependPath(`api/datasets/${props.item.id}/display?to_ext=${props.item.extension}`);
});

function onCopyLink() {
    const msg = localize("Link copied to your clipboard");
    sendToClipboard(absPath(downloadUrl.value), msg);
}

function onDownload(resource: string) {
    window.location.href = resource;
}

function onHighlight() {
    emit("toggleHighlights");
}

function onError() {
    router.push(`/datasets/${props.item.id}/error`);
}

function onInfo() {
    router.push(`/datasets/${props.item.id}/details`);
}

function onVisualize() {
    router.push(`/datasets/${props.item.id}/visualize`);
}

function onRerun() {
    router.push(`/root?job_id=${props.item.creating_job}`);
}

async function onRecrypt() {
    try {
        let recryptResponse = await axios.post("https://localhost:61357/recrypt_header", {
            crypt4gh_header: props.item.metadata_crypt4gh_header,
        });

        let copyHdaResponse = await axios.post(`${getAppRoot()}api/histories/${props.item.history_id}/contents/datasets`, {
            source: "hda",
            content: props.item.id,
        })

        let editHdaResponse = await axios.put(`${getAppRoot()}api/histories/${props.item.history_id}/contents/datasets/${copyHdaResponse.data.id}`, {
            tags: ["Recrypted_for_compute", recryptResponse.data.crypt4gh_compute_keypair_id],
            metadata: {
              ...recryptResponse.data
            }
        })

        let datatypeDetectResponse = await setAttributes(copyHdaResponse.data.id, {}, "autodetect")

        historyStore.loadCurrentHistory();
    } catch (err) {
        console.error(JSON.stringify(err, Object.getOwnPropertyNames(err)));
    }
}

</script>

<template>
    <div class="dataset-actions mb-1">
        <div class="clearfix">
            <div class="btn-group float-left">
                <BButton
                    v-if="showError"
                    v-b-tooltip.hover
                    class="px-1"
                    title="Error"
                    size="sm"
                    variant="link"
                    :href="reportErrorUrl"
                    @click.prevent.stop="onError">
                    <FontAwesomeIcon :icon="faBug" />
                </BButton>

                <DatasetDownload v-if="showDownloads" :item="item" @on-download="onDownload" />

                <BButton
                    v-if="showDownloads"
                    v-b-tooltip.hover
                    class="px-1"
                    title="Copy Link"
                    size="sm"
                    variant="link"
                    @click.stop="onCopyLink">
                    <FontAwesomeIcon :icon="faLink" />
                </BButton>

                <BButton
                    v-if="showInfo"
                    v-b-tooltip.hover
                    class="info-btn px-1"
                    title="Dataset Details"
                    size="sm"
                    variant="link"
                    :href="showDetailsUrl"
                    @click.prevent.stop="onInfo">
                    <FontAwesomeIcon :icon="faInfoCircle" />
                </BButton>

                <BButton
                    v-if="showVisualizations"
                    v-b-tooltip.hover
                    class="visualize-btn px-1"
                    title="Visualize"
                    size="sm"
                    variant="link"
                    :href="visualizeUrl"
                    @click.prevent.stop="onVisualize">
                    <FontAwesomeIcon :icon="faChartBar" />
                </BButton>

                <BButton
                    v-if="showHighlight"
                    v-b-tooltip.hover
                    class="highlight-btn px-1"
                    title="Show Related Items"
                    size="sm"
                    variant="link"
                    @click.stop="onHighlight">
                    <FontAwesomeIcon :icon="faSitemap" />
                </BButton>

                <BButton
                    v-if="writable && showRerun"
                    v-b-tooltip.hover
                    class="rerun-btn px-1"
                    title="Run Job Again"
                    size="sm"
                    variant="link"
                    :href="rerunUrl"
                    @click.prevent.stop="onRerun">
                    <FontAwesomeIcon :icon="faRedo" />
                </BButton>

                <BButton
                    v-if="showRecrypt"
                    v-g-tooltip.hover
                    class="px-1"
                    title="Recrypt Crypt4GH-encrypted dataset"
                    size="sm"
                    variant="link"
                    @click.prevent.stop="onRecrypt">
                    <FontAwesomeIcon :icon="faKey" />
                </BButton>
            </div>
        </div>
    </div>
</template>
